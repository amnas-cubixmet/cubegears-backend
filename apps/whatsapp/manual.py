"""Authenticated Click-to-Chat preparation, separate from Meta API delivery.

Preparing a message never proves that it was sent. Private capability URLs
are returned only to a permitted staff session and never stored in log text.
"""
import uuid
from urllib.parse import quote

from django.shortcuts import get_object_or_404
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import user_has_permission
from apps.customers.models import Customer
from apps.customers.portal_tokens import ensure_portal_access, issue_link
from apps.invoices.models import Invoice
from apps.jobs.models import Job, JobEstimate
from apps.jobs.workflow import workflow_state
from apps.customers.estimate_review import public_estimate
from .models import WhatsAppNotificationLog
from .phone import normalize_recipient


def click_to_chat(phone, message):
    return f"https://wa.me/{phone}?text={quote(message, safe='')}"


def _require(request, permission):
    if not user_has_permission(request.user, permission):
        raise PermissionDenied("You do not have permission to share this record.")


class WhatsAppManualShareView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        kind = str(request.data.get("targetType") or "").lower().strip()
        ident = request.data.get("targetId")
        try:
            ident = uuid.UUID(str(ident))
        except (ValueError, TypeError, AttributeError):
            return Response({"message": "Choose a valid record to share."}, status=400)
        company = request.user.company
        if company is None:
            raise PermissionDenied("Sign in to a workshop before sharing records.")
        estimate = None

        if kind == "customer":
            _require(request, "customers.view")
            customer = get_object_or_404(Customer, company=company, pk=ident)
            job, invoice = None, None
        elif kind in {"job", "job_status", "ready_delivery"}:
            _require(request, "jobs.view")
            job = get_object_or_404(
                Job.objects.select_related("customer", "vehicle"),
                company=company, pk=ident, customer__company=company,
            )
            customer, invoice = job.customer, None
            if kind == "ready_delivery" and job.status != "Ready for Delivery":
                return Response({"message": "This vehicle is not marked ready for delivery."}, status=409)
        elif kind == "estimate":
            _require(request, "jobs.view")
            estimate = get_object_or_404(
                JobEstimate.objects.select_related(
                    "job__customer", "job__vehicle", "job__company"
                ),
                company=company, pk=ident, job__company=company,
                job__customer__company=company,
            )
            job = estimate.job
            latest_id = job.estimates.order_by("-version", "-created_at").values_list(
                "pk", flat=True,
            ).first()
            if (latest_id != estimate.pk or estimate.status != "Awaiting Customer"
                    or workflow_state(job)["current"] != "estimate"
                    or job.vehicle.customer_id != job.customer_id
                    or not public_estimate(estimate)["reviewable"]):
                return Response({
                    "message": "Only the latest published, itemized estimate can be shared."
                }, status=409)
            customer, invoice = job.customer, None
        elif kind in {"invoice", "payment_reminder"}:
            _require(request, "invoices.view")
            invoice = get_object_or_404(
                Invoice.objects.select_related("customer", "job"),
                company=company, pk=ident, customer__company=company,
            )
            if not invoice.finalized_at or invoice.status in {"Draft", "Cancelled"}:
                return Response({"message": "Only finalized, published invoices can be shared."}, status=409)
            if kind == "payment_reminder" and invoice.balance <= 0:
                return Response({"message": "This invoice has no outstanding balance."}, status=409)
            customer, job = invoice.customer, invoice.job
        elif kind == "log":
            if not (user_has_permission(request.user, "settings.manage") or
                    user_has_permission(request.user, "company.manage")):
                raise PermissionDenied("Workshop administrator permission is required.")
            log = get_object_or_404(
                WhatsAppNotificationLog.objects.select_related("customer", "job"),
                company=company, pk=ident, status__in=["failed", "skipped"],
            )
            customer, job, invoice = log.customer, log.job, None
            if not customer:
                return Response({"message": "No customer is attached to this failed notification."}, status=409)
            if log.event in {"estimate_shared", "estimate_reminder"}:
                from .tasks import _active_estimate
                estimate = _active_estimate(log.event, log.idempotency_key, customer, job)
                if estimate is None:
                    return Response({
                        "message": "This estimate is no longer awaiting customer review."
                    }, status=409)
        else:
            return Response({"message": "Select customer, job, job_status, ready_delivery, estimate, invoice, payment_reminder, or failed log."}, status=400)

        if not customer or customer.company_id != company.pk:
            return Response({"message": "Customer is unavailable for this workshop."}, status=404)

        # Prefer a valid explicit WhatsApp contact; fall back to the registered
        # customer phone when that legacy optional field is incomplete.
        # Only an explicitly Indian workshop may normalize legacy 10-digit mobiles.
        number = (
            normalize_recipient(customer.whatsapp, country=company.country)
            or normalize_recipient(customer.phone, country=company.country)
        )
        if not number:
            return Response({
                "message": "Customer WhatsApp number must include country code, e.g. +919876543210."
            }, status=400)

        access = ensure_portal_access(customer)
        if not access.is_active:
            return Response({"message": "Tracking link is revoked. Re-enable it before sharing."}, status=409)
        try:
            secure_url = issue_link(access)
        except ValueError:
            return Response({"message": "Tracking link cannot be recovered. Regenerate the customer link."}, status=409)

        workshop = company.name
        if estimate is not None:
            message = (
                f"Hello {customer.name}, Estimate V{estimate.version} for job card "
                f"{job.job_number} at {workshop} is ready for your review "
                f"(total INR {estimate.total}). Review the itemized quote and "
                f"accept or decline in your secure customer portal: {secure_url}"
            )
        elif kind == "payment_reminder":
            message = (
                f"Hello {customer.name}, this is a payment reminder from {workshop} "
                f"for invoice {invoice.number}. The outstanding amount is INR {invoice.balance}. "
                f"Please review the invoice securely through your customer portal: {secure_url}"
            )
        elif invoice:
            message = (
                f"Hello {customer.name}, your invoice {invoice.number} from {workshop} is ready. "
                f"View your workshop service updates securely: {secure_url}"
            )
        elif kind == "ready_delivery":
            message = (
                f"Hello {customer.name}, your vehicle is ready for delivery from {workshop}. "
                f"Review job card {job.job_number} and your service updates securely: {secure_url}"
            )
        elif job:
            message = (
                f"Hello {customer.name}, here is the update for job card {job.job_number} "
                f"at {workshop}. Track your vehicle service securely: {secure_url}"
            )
        else:
            message = (
                f"Hello {customer.name}, track your vehicle service at {workshop} "
                f"using your private link: {secure_url}"
            )

        WhatsAppNotificationLog.objects.create(
            company=company,
            branch=customer.branch,
            customer=customer,
            job=job,
            event="manual_" + kind,
            idempotency_key="manual:" + uuid.uuid4().hex,
            phone=number,
            status="manual_prepared",
        )
        response = Response({
            "url": click_to_chat(number, message),
            "preview": message,
            "status": "manual_prepared",
            "note": "WhatsApp opens with a draft. The staff member must press Send; delivery cannot be verified here.",
        })
        response["Cache-Control"] = "no-store"
        return response
