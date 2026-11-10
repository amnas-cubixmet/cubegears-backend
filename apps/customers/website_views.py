"""Tenant-isolated, cookie-authenticated customer website endpoints.

The incoming workshop hostname is signed by the trusted same-origin BFF;
client-supplied tenant IDs are never authorization inputs.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from mimetypes import guess_type

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.companies.models import Company, CompanyDomain, CompanyWebsiteSettings, CompanyWebsiteSubdomain
from apps.customers.models import CustomerPortalAccess
from apps.customers.portal_tokens import resolve_link
from apps.customers.estimate_review import public_estimate
from apps.customers.estimate_feedback import (
    public_decline_feedback, validated_estimate_feedback,
)
from apps.customers.portal_views import _job_public
from apps.invoices.models import Invoice
from apps.jobs.models import Job, JobActivity, JobEstimate
from apps.jobs.workflow import workflow_state
from apps.notifications.estimate_responses import notify_customer_estimate_response
from apps.vehicles.models import Vehicle, VehicleInspectionPhoto
from apps.vehicles.photo_views import _file_response, _image_bytes


def _secret():
    return os.environ.get("PORTAL_PROXY_SHARED_SECRET", "")


def _tenant(request):
    """Fail closed unless the server-side proxy authenticated the hostname."""
    host = str(request.headers.get("X-CG-Portal-Host") or "").strip().lower()
    ts = str(request.headers.get("X-CG-Portal-TS") or "")
    sig = str(request.headers.get("X-CG-Portal-Sig") or "")
    key = _secret()
    if (
        len(key) < 32
        or len(host) > 255
        or not re.fullmatch(r"[a-z0-9.-]+(?::[0-9]{1,5})?", host)
        or not re.fullmatch(r"\d{10,11}", ts)
        or not re.fullmatch(r"[a-f0-9]{64}", sig)
        or abs(time.time() - int(ts)) > 120
    ):
        raise Http404("Website not found.")
    expected = hmac.new(key.encode(), (ts + ":" + host).encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        raise Http404("Website not found.")
    hostname = host.split(":", 1)[0]
    if settings.DEBUG and hostname in {"localhost", "127.0.0.1"}:
        slug = os.environ.get("PORTAL_DEV_COMPANY_SLUG", "")
        if slug:
            return get_object_or_404(Company, slug=slug, is_active=True)
    # Local custom-domain simulation. Explicit mapping is never used outside
    # Django DEBUG, and .test names never require real DNS, Vercel or SSL.
    # A signed BFF hostname alone does not grant access to an arbitrary tenant.
    if settings.DEBUG and hostname.endswith(".test"):
        try:
            preview_map = json.loads(os.environ.get("PORTAL_DEV_CUSTOM_DOMAIN_MAP", "{}"))
        except (TypeError, ValueError):
            preview_map = {}
        slug = preview_map.get(hostname) if isinstance(preview_map, dict) else None
        if not isinstance(slug, str) or not slug or len(slug) > 180:
            raise Http404("Website not found.")
        return get_object_or_404(Company, slug=slug, is_active=True)

    root = os.environ.get("CUSTOMER_PORTAL_ROOT_DOMAIN", "cubegears.com").lower().strip(".")
    if settings.DEBUG and hostname.endswith(".localhost"):
        root = "localhost"
    if hostname.endswith("." + root):
        label = hostname[:-(len(root) + 1)]
        if re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?", label):
            entry = CompanyWebsiteSubdomain.objects.select_related("company").filter(
                label=label, company__is_active=True
            ).first()
            if entry:
                return entry.company
            # Legacy test/admin-created companies may not yet have a registry
            # row. Never fall back to the login slug of a renamed website.
            fallback = Company.objects.filter(
                slug=label, is_active=True, website_subdomain__isnull=True
            ).first()
            if fallback:
                return fallback
    domain = CompanyDomain.objects.select_related("company").filter(
        hostname=hostname, status=CompanyDomain.ACTIVE,
        is_approved=True, dns_configured=True, ownership_verified=True,
        ssl_status="ready", verified_at__isnull=False, company__is_active=True
    ).first()
    if domain:
        return domain.company
    raise Http404("Website not found.")


def _csrf_key():
    return str(getattr(settings, "SECRET_KEY", "")).encode()


def _make_csrf():
    nonce = secrets.token_urlsafe(32)
    digest = hmac.new(_csrf_key(), ("portal-csrf:" + nonce).encode(), hashlib.sha256).hexdigest()
    return nonce + "." + digest


def _verify_csrf(request):
    cookie = request.COOKIES.get("cg_portal_csrf", "")
    provided = request.headers.get("X-CG-CSRF", "")
    if not cookie or len(cookie) > 160 or not hmac.compare_digest(cookie, provided):
        return False
    try:
        nonce, mac = cookie.rsplit(".", 1)
    except ValueError:
        return False
    if len(nonce) < 32 or len(mac) != 64:
        return False
    expected = hmac.new(_csrf_key(), ("portal-csrf:" + nonce).encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, mac)


def _no_store(response):
    response["Cache-Control"] = "private, no-store"
    response["Referrer-Policy"] = "no-referrer"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def _session_access(request, company):
    auth = request.session.get("cg_customer_portal")
    if not isinstance(auth, dict) or str(auth.get("company")) != str(company.pk):
        return None
    now = int(time.time())
    if (
        now - int(auth.get("started", 0)) > 12 * 3600
        or now - int(auth.get("last_seen", 0)) > 30 * 60
        or now < int(auth.get("started", 0))
    ):
        request.session.pop("cg_customer_portal", None)
        return None
    access = CustomerPortalAccess.objects.select_related("customer", "company").filter(
        pk=auth.get("access"), customer_id=auth.get("customer"), company=company,
        is_active=True, customer__status="active", company__is_active=True,
    ).first()
    # A rotated capability is a new authorization version. A browser session
    # minted from the previous link must not survive rotation (or revocation).
    if not access or not hmac.compare_digest(
        str(auth.get("version") or ""), str(access.token_hash if access else ""),
    ):
        request.session.pop("cg_customer_portal", None)
        return None
    auth["last_seen"] = now
    request.session["cg_customer_portal"] = auth
    return access


def _branding(company):
    config = CompanyWebsiteSettings.objects.filter(company=company).first()
    return {
        "name": company.name,
        "publicName": (config.public_name if config else "") or company.name,
        "phone": (config.contact_phone if config else "") or company.phone,
        "whatsapp": (config.whatsapp_phone if config else "") or company.phone,
        "address": company.address,
        "city": company.city,
        "description": config.description if config else "",
        "welcomeMessage": config.welcome_message if config else "",
        "primaryColor": config.primary_color if config else "#116f69",
        "secondaryColor": config.secondary_color if config else "#e4f2ec",
        "hasLogo": bool(company.logo_image),
    }


def _customer_quote(estimate, history=None):
    result = public_estimate(estimate)
    # Resolve historical version decisions from immutable audit records, not
    # the latest-only workflow_progress pointer.
    record = (history or {}).get(str(estimate.pk))
    if record:
        result["decisionReceipt"] = record
        return result
    # Compatibility for older decisions whose history has no receipt record.
    progress = (estimate.job.workflow_progress or {}).get("customerEstimateDecision") or {}
    if progress.get("estimateId") == str(estimate.pk) and progress.get("version") == estimate.version:
        if progress.get("status") in {"Accepted", "Declined"}:
            receipt = {
                "id": progress.get("receiptId") or None,
                "status": progress["status"],
                "respondedAt": progress.get("respondedAt"),
            }
            if progress["status"] == "Declined":
                receipt["declineFeedback"] = public_decline_feedback(progress)
            result["decisionReceipt"] = receipt
    return result


def _dashboard(access):
    company, customer = access.company, access.customer
    vehicles = list(customer.vehicles.filter(company=company).order_by("registration")[:100])
    jobs = list(Job.objects.filter(
        company=company, customer=customer, vehicle__customer=customer
    ).select_related("vehicle").order_by("-created_at")[:100])
    estimates = list(JobEstimate.objects.filter(
        company=company, job__customer=customer, job__company=company,
        job__vehicle__customer=customer,
        status__in=["Awaiting Customer", "Customer Approved", "Customer Rejected", "Approved"]
    ).select_related("job").order_by("-created_at")[:100])
    quote_ids = {str(e.pk) for e in estimates}
    receipts = {}
    if quote_ids:
        # Bounded to this customer's own Job Cards and listed quote versions.
        activities = JobActivity.objects.filter(
            company=company, job__company=company, job__customer=customer,
            job__vehicle__customer=customer,
            job_id__in={e.job_id for e in estimates},
            event__in=["Customer Estimate Accepted", "Customer Estimate Declined"],
        ).order_by("created_at")
        for activity in activities:
            detail = activity.metadata or {}
            estimate_key = str(detail.get("estimateId") or "")
            if estimate_key not in quote_ids:
                continue
            declined = activity.event == "Customer Estimate Declined"
            receipt = {
                "id": str(activity.pk),
                "status": "Declined" if declined else "Accepted",
                "respondedAt": activity.created_at.isoformat(),
            }
            if declined:
                receipt["declineFeedback"] = public_decline_feedback(detail)
            receipts[estimate_key] = receipt
    invoices = Invoice.objects.filter(
        company=company, customer=customer, finalized_at__isnull=False
    ).exclude(status__in=["Draft", "Cancelled"]).order_by("-date")[:100]
    photos = VehicleInspectionPhoto.objects.filter(
        company=company, vehicle__customer=customer, published=True
    ).order_by("-captured_at")[:150]
    pending_photos = VehicleInspectionPhoto.objects.filter(
        company=company, vehicle__customer=customer,
        published=False, submitted_by_customer=True,
    ).order_by("-captured_at")[:20]
    return {
        "customer": {"name": customer.name},
        "vehicles": [
            {"id": str(v.pk), "registration": v.registration,
             "model": " ".join(filter(None, [v.make, v.model, v.variant])), "year": v.year}
            for v in vehicles
        ],
        "jobs": [_job_public(j) for j in jobs],
        "estimates": [_customer_quote(e, receipts) for e in estimates],
        "invoices": [
            {"jobId": str(inv.job_id) if inv.job_id else None,
             "number": inv.number, "date": inv.date, "status": inv.status,
             "total": str(inv.total), "paid": str(inv.paid), "balance": str(inv.balance),
             "kind": inv.kind} for inv in invoices
        ],
        "photos": [
            {"id": str(p.pk), "vehicleId": str(p.vehicle_id),
             "jobId": str(p.job_id) if p.job_id else None,
             "category": p.category, "phase": p.phase, "caption": p.caption,
             "capturedAt": p.captured_at} for p in photos
        ],
        "pendingPhotos": [
            {"id": str(p.pk), "vehicleId": str(p.vehicle_id),
             "caption": p.caption, "capturedAt": p.captured_at}
            for p in pending_photos
        ],
    }


class CustomerWebsiteThrottle(ScopedRateThrottle):
    """Use the client identity supplied by the trusted proxy, not its egress IP."""
    def get_ident(self, request):
        ident = request.headers.get("X-CG-Portal-Client", "") or super().get_ident(request)
        return str(ident)[:64]


@method_decorator(never_cache, name="dispatch")
class PortalBaseView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [CustomerWebsiteThrottle]
    throttle_scope = "customer_website"


class WebsiteBrandingView(PortalBaseView):
    def get(self, request):
        company = _tenant(request)
        response = _no_store(Response(_branding(company)))
        response.set_cookie(
            "cg_portal_csrf", _make_csrf(), max_age=3600, path="/",
            secure=not settings.DEBUG, httponly=False, samesite="Lax",
        )
        return response


class WebsiteSessionView(PortalBaseView):
    def get(self, request):
        company = _tenant(request)
        access = _session_access(request, company)
        if not access:
            return _no_store(Response({"message": "Customer session required."}, status=401))
        return _no_store(Response(_dashboard(access)))

    def post(self, request):
        company = _tenant(request)
        if not _verify_csrf(request):
            return _no_store(Response({"message": "Invalid request token."}, status=403))
        token = request.data.get("token")
        access = resolve_link(token)
        if not access or access.company_id != company.pk:
            return _no_store(Response({"message": "Access link invalid."}, status=404))
        request.session.flush()
        now = int(time.time())
        request.session["cg_customer_portal"] = {
            "company": str(company.pk), "customer": str(access.customer_id),
            "access": str(access.pk), "version": access.token_hash,
            "started": now, "last_seen": now,
        }
        request.session.set_expiry(0)
        return _no_store(Response({"authenticated": True}))


class WebsiteLogoutView(PortalBaseView):
    def post(self, request):
        _tenant(request)
        if not _verify_csrf(request):
            return _no_store(Response({"message": "Invalid request token."}, status=403))
        request.session.flush()
        return _no_store(Response({"authenticated": False}))


class WebsiteEstimateDecisionView(PortalBaseView):
    """Customer's explicit response to one staff-published immutable quote.

    This records customer intent and an audit event. It never charges money,
    advances the Job Card, issues inventory or finalizes an invoice.
    """
    @transaction.atomic
    def post(self, request, estimate_id):
        company = _tenant(request)
        if not _verify_csrf(request):
            return _no_store(Response({"message": "Invalid request token."}, status=403))
        access = _session_access(request, company)
        if not access:
            return _no_store(Response({"message": "Customer session required."}, status=401))
        decision = request.data.get("decision")
        if decision not in {"accept", "decline"}:
            return _no_store(Response({"message": "Choose accept or decline."}, status=400))
        feedback = validated_estimate_feedback(request.data, decision)

        found = JobEstimate.objects.filter(
            pk=estimate_id, company=company,
            job__company=company, job__customer=access.customer,
            job__vehicle__customer=access.customer,
        ).values_list("job_id", flat=True).first()
        if not found:
            raise Http404("Estimate not found.")
        # Lock the job first, like the workshop approval API, so competing
        # staff/customer decisions cannot advance an outdated quote.
        job = get_object_or_404(
            Job.objects.select_for_update(), pk=found, company=company,
            customer=access.customer, vehicle__customer=access.customer,
        )
        estimate = get_object_or_404(
            JobEstimate.objects.select_for_update(),
            pk=estimate_id, job=job, company=company,
        )
        latest = job.estimates.order_by("-version", "-created_at").first()
        if not latest or latest.pk != estimate.pk or workflow_state(job)["current"] != "estimate":
            return _no_store(Response({"message": "This quotation is no longer open."}, status=409))
        if str(request.data.get("version")) != str(estimate.version):
            return _no_store(Response({"message": "Quotation version changed. Refresh before responding."}, status=409))

        review = public_estimate(estimate)
        supplied_hash = str(request.data.get("reviewHash") or "")
        if not review["reviewable"] or not review["reviewHash"] or not hmac.compare_digest(
            supplied_hash, review["reviewHash"]
        ):
            return _no_store(Response({
                "message": "Quotation breakdown is missing or has changed. Refresh and review before responding."
            }, status=409))

        target = "Customer Approved" if decision == "accept" else "Customer Rejected"
        if estimate.status == target:
            # Exact repeat is idempotent; altered notes/reason after submission
            # must not silently change a permanent audit decision.
            previous = (job.workflow_progress or {}).get("customerEstimateDecision") or {}
            if decision == "decline" and (
                previous.get("estimateId") == str(estimate.pk)
                and (
                    previous.get("declineReason", "unspecified") != feedback["reason"]
                    or previous.get("declineNote", "") != feedback["note"]
                )
            ):
                return _no_store(Response({"message": "This decision was already recorded with different feedback."}, status=409))
            return _no_store(Response({
                "id": str(estimate.pk), "version": estimate.version,
                "status": estimate.status, "total": str(estimate.total),
                "decisionReceipt": _customer_quote(estimate).get("decisionReceipt"),
            }))
        if estimate.status != "Awaiting Customer":
            return _no_store(Response({"message": "This quote is not available for customer review."}, status=409))
        if (job.inspection or {}).get("revisedAt"):
            from django.utils.dateparse import parse_datetime
            revised = parse_datetime(job.inspection["revisedAt"])
            if revised and estimate.created_at < revised:
                return _no_store(Response({"message": "Inspection changed. Ask for a revised estimate."}, status=409))

        estimate.status = target
        estimate.save(update_fields=["status", "updated_at"])
        now = timezone.now().isoformat()
        activity = JobActivity.objects.create(
            company=company, branch=job.branch, job=job, actor=None,
            event="Customer Estimate Accepted" if decision == "accept" else "Customer Estimate Declined",
            description=f"Customer responded to estimate V{estimate.version} via secure portal",
            metadata={
                "estimateId": str(estimate.pk), "version": estimate.version,
                "total": str(estimate.total), "source": "customer_portal_session",
                "reviewHash": review["reviewHash"],
                **({
                    "declineReason": feedback["reason"],
                    "declineNote": feedback["note"],
                } if feedback else {}),
            },
        )
        progress = dict(job.workflow_progress or {})
        progress["customerEstimateDecision"] = {
            "status": "Accepted" if decision == "accept" else "Declined",
            "estimateId": str(estimate.pk), "version": estimate.version,
            "total": str(estimate.total), "respondedAt": now,
            "receiptId": str(activity.pk), "reviewHash": review["reviewHash"],
            "confirmationSource": "customer_portal_session",
            **({
                "declineReason": feedback["reason"],
                "declineNote": feedback["note"],
            } if feedback else {}),
        }
        job.workflow_progress = progress
        job.save(update_fields=["workflow_progress", "updated_at"])
        # Keep this inside the same transaction as the customer decision.
        # Per-recipient inbox alerts are emitted only after a successful
        # first response; an idempotent retry returns above without repeats.
        notify_customer_estimate_response(job, estimate, decision, activity)
        return _no_store(Response({
            "id": str(estimate.pk), "version": estimate.version,
            "status": estimate.status, "total": str(estimate.total),
            "decisionReceipt": {
                "id": str(activity.pk),
                "status": "Accepted" if decision == "accept" else "Declined",
                "respondedAt": now,
                **({"declineFeedback": feedback} if feedback else {}),
            },
        }))


class WebsiteLogoView(PortalBaseView):
    def get(self, request):
        company = _tenant(request)
        if not company.logo_image:
            raise Http404("Logo unavailable.")
        try:
            content = company.logo_image.open("rb")
        except (FileNotFoundError, OSError, ValueError):
            raise Http404("Logo unavailable.")
        response = FileResponse(
            content, content_type=guess_type(company.logo_image.name)[0] or "application/octet-stream"
        )
        return _no_store(response)


class WebsitePhotoView(PortalBaseView):
    def get(self, request, photo_id):
        company = _tenant(request)
        access = _session_access(request, company)
        if not access:
            raise Http404("Photo unavailable.")
        photo = get_object_or_404(
            VehicleInspectionPhoto, pk=photo_id, company=company,
            vehicle__customer=access.customer, published=True
        )
        return _no_store(_file_response(photo))
