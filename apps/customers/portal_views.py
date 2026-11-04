"""Dedicated allowlisted, tenant-safe public tracking APIs (no staff serializer)."""
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.views.decorators.cache import never_cache
from django.utils.decorators import method_decorator
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from apps.accounts.permissions import user_has_permission
from apps.customers.models import Customer
from apps.customers.portal_tokens import (
    ensure_portal_access, issue_link, resolve_link, revoke_link, rotate_link,
)
from apps.jobs.models import Job, JobEstimate
from apps.invoices.models import Invoice
from apps.vehicles.models import VehicleInspectionPhoto


def _job_stage(job):
    status = job.status
    inspection = (job.inspection or {}).get("status")
    if status == "Delivered":
        return "delivered"
    if status == "Ready for Delivery":
        return "ready"
    if status == "QC":
        return "quality_check"
    if status in {"In Progress", "Waiting for Parts"}:
        return "work_in_progress"
    if status == "Approved":
        return "work_started"
    if inspection == "Completed" or status == "Estimate Pending":
        return "inspection_completed"
    if status == "Inspection":
        return "inspection"
    return "received"


def _job_public(job):
    # No costs, private inspection findings, employee names or internal notes.
    return {
        "id": str(job.id),
        "number": job.job_number,
        "vehicleId": str(job.vehicle_id),
        "vehicleRegistration": job.vehicle.registration,
        "vehicleModel": " ".join(filter(None, [job.vehicle.make, job.vehicle.model])),
        "createdAt": job.created_at,
        "status": job.status,
        "stage": _job_stage(job),
        "deliveredAt": job.delivered_at,
    }


class CustomerPortalLinkView(APIView):
    permission_classes = [IsAuthenticated]

    def _customer(self, request, customer_id, edit=False):
        perm = "customers.edit" if edit else "customers.view"
        if not user_has_permission(request.user, perm):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("You do not have permission to manage customer tracking.")
        return get_object_or_404(Customer, pk=customer_id, company=request.user.company)

    def get(self, request, customer_id):
        access = ensure_portal_access(self._customer(request, customer_id))
        result = {"enabled": access.is_active, "customerId": str(customer_id)}
        if access.is_active:
            result["url"] = issue_link(access)
        return Response(result)

    def post(self, request, customer_id):
        customer = self._customer(request, customer_id, edit=True)
        access = ensure_portal_access(customer)
        action = request.data.get("action")
        if action == "revoke":
            revoke_link(access)
            return Response({"enabled": False, "url": None})
        if action in {"rotate", "enable"}:
            return Response({"enabled": True, "url": rotate_link(access)})
        return Response({"message": "Choose rotate, revoke, or enable."}, status=400)


@method_decorator(never_cache, name="dispatch")
class CustomerPortalDetailView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "customer_portal"

    def get(self, request, token):
        access = resolve_link(token)
        if not access:
            raise Http404("Tracking link not found.")
        customer = access.customer
        company = access.company
        vehicles = list(customer.vehicles.filter(company=company).order_by("registration")[:100])
        jobs = list(
            Job.objects.filter(company=company, customer=customer, vehicle__customer=customer)
            .select_related("vehicle").order_by("-created_at")[:100]
        )
        approved_estimates = JobEstimate.objects.filter(
            company=company, job__customer=customer, job__company=company, status="Approved",
        ).order_by("-created_at")[:100]
        published_invoices = Invoice.objects.filter(
            company=company, customer=customer, finalized_at__isnull=False,
        ).exclude(status__in=["Draft", "Cancelled"]).order_by("-date")[:100]
        # Explicit allowlists only. Do not include phone/email, costs, private
        # inspection JSON, private work updates or staff-only JobPhoto URLs.
        return Response({
            "workshop": {
                "name": company.name,
                "phone": company.phone,
                "address": company.address,
                "city": company.city,
            },
            "vehicles": [
                {
                    "id": str(v.id), "registration": v.registration,
                    "model": " ".join(filter(None, [v.make, v.model, v.variant])),
                    "year": v.year,
                } for v in vehicles
            ],
            "jobs": [_job_public(job) for job in jobs],
            "photos": [
                {"id": str(photo.pk), "vehicleId": str(photo.vehicle_id),
                 "jobId": str(photo.job_id) if photo.job_id else None,
                 "category": photo.category, "phase": photo.phase,
                 "caption": photo.caption, "capturedAt": photo.captured_at,
                 "url": f"/c/{token}/photos/{photo.pk}"}
                for photo in VehicleInspectionPhoto.objects.filter(
                    company=company, vehicle__customer=customer, published=True,
                ).order_by("-captured_at")[:150]
            ],
            "estimates": [
                {"jobId": str(e.job_id), "version": e.version,
                 "status": e.status, "total": str(e.total)}
                for e in approved_estimates
            ],
            "invoices": [
                {"jobId": str(inv.job_id) if inv.job_id else None, "number": inv.number,
                 "date": inv.date, "status": inv.status, "total": str(inv.total),
                 "kind": inv.kind}
                for inv in published_invoices
            ],
        })
