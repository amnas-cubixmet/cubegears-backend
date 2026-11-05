"""Authenticated Click-to-Chat preparation, separate from Meta API delivery.

Preparing a message never proves that it was sent. Private capability URLs
are returned only to a permitted staff session and never stored in log text.
"""
import re
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
from apps.jobs.models import Job
from .models import WhatsAppNotificationLog


def normalize_recipient(value):
    raw = str(value or "").strip()
    digits = re.sub(r"\\D", "", raw)
    # Without country code, local ten-digit numbers are ambiguous.
    if not raw or (not raw.startswith("+") and len(digits) < 11):
        return ""
    if not re.fullmatch(r"[1-9][0-9]{7,14}", digits):
        return ""
    return digits


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
        company = request.user.company

        if kind == "customer":
            _require(request, "customers.view")
            customer = get_object_or_404(Customer, company=company, pk=ident)
            job, invoice = None, None
        elif kind == "job":
            _require(request, "jobs.view")
            job = get_object_or_404(
                Job.objects.select_related("customer", "vehicle"),
                company=company, pk=ident, customer__company=company,
            )
            customer, invoice = job.customer, None
        elif kind == "invoice":
            _require(request, "invoices.view")
            invoice = get_object_or_404(
                Invoice.objects.select_related("customer", "job"),
                company=company, pk=ident, customer__company=company,
            )
            if not invoice.finalized_at or invoice.status in {"Draft", "Cancelled"}:
                return Response({"message": "Only finalized, published invoices can be shared."}, status=409)
            customer, job = invoice.customer, invoice.job
        elif kind == "log":
            _require(request, "settings.manage")
            log = get_object_or_404(
                WhatsAppNotificationLog.objects.select_related("customer", "job"),
                company=company, pk=ident, status__in=["failed", "skipped"],
            )
            customer, job, invoice = log.customer, log.job, None
            if not customer:
                return Response({"message": "No customer is attached to this failed notification."}, status=409)
        else:
            return Response({"message": "Select customer, job, invoice, or failed log."}, status=400)

        if not customer or customer.company_id != company.pk:
            return Response({"message": "Customer is unavailable for this workshop."}, status=404)

        number = normalize_recipient(customer.whatsapp or customer.phone)
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
        if invoice:
            message = (
                f"Hello {customer.name}, your invoice {invoice.number} from {workshop} is ready. "
                f"View your workshop service updates securely: {secure_url}"
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
