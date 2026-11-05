"""Tenant-scoped Customer Website branding and Vercel domain management."""
import os
import re

from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsCompanyAdmin
from .models import Company, CompanyDomain, CompanyDomainAudit, CompanyWebsiteSettings
from .vercel_domains import (
    VercelDomainError, VercelDomains, record_domain_event, refresh_domain,
)


class SettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = CompanyWebsiteSettings
        fields = (
            "public_name", "description", "welcome_message",
            "contact_phone", "whatsapp_phone", "primary_color", "secondary_color",
        )

    def validate_primary_color(self, color):
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            raise serializers.ValidationError("Enter a valid six-digit hex color.")
        return color.lower()

    def validate_secondary_color(self, color):
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            raise serializers.ValidationError("Enter a valid six-digit hex color.")
        return color.lower()


def _root_domain():
    return os.environ.get("CUSTOMER_PORTAL_ROOT_DOMAIN", "cubegears.com").strip(".").lower()


class CompanyWebsiteSettingsView(APIView):
    permission_classes = [IsCompanyAdmin]

    def _settings(self, request):
        website, _ = CompanyWebsiteSettings.objects.get_or_create(
            company=request.user.company
        )
        return website

    def get(self, request):
        company = request.user.company
        settings_data = SettingsSerializer(self._settings(request)).data
        settings_data["subdomain"] = company.slug + "." + _root_domain()
        settings_data["hasLogo"] = bool(company.logo_image)
        return Response(settings_data)

    def patch(self, request):
        current = self._settings(request)
        serializer = SettingsSerializer(current, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(SettingsSerializer(current).data)


def _domain_payload(domain):
    return {
        "id": domain.id, "hostname": domain.hostname,
        "status": domain.status, "isPrimary": domain.is_primary,
        "verifiedAt": domain.verified_at, "dnsProvider": domain.dns_provider,
        "dnsRecords": domain.dns_records, "verificationRecords": domain.verification_records,
        "dnsConfigured": domain.dns_configured, "ownershipVerified": domain.ownership_verified,
        "sslStatus": domain.ssl_status, "isApproved": domain.is_approved,
        "lastError": domain.last_error, "lastCheckedAt": domain.last_checked_at,
    }


def _validated_hostname(raw):
    hostname = str(raw or "").strip().lower().rstrip(".")
    if len(hostname) > 253 or not hostname or "://" in hostname or "/" in hostname or "*" in hostname:
        raise serializers.ValidationError({"hostname": "Enter a valid domain without scheme or path."})
    try:
        hostname = hostname.encode("idna").decode("ascii")
    except (UnicodeError, ValueError):
        raise serializers.ValidationError({"hostname": "Invalid domain name."}) from None
    labels = hostname.split(".")
    if (
        len(labels) < 2 or len(hostname) > 253 or labels[-1].isdigit()
        or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part)
               for part in labels)
        or labels[-1] in {"local", "localhost", "internal", "test", "invalid", "example"}
    ):
        raise serializers.ValidationError({"hostname": "Enter a public domain you control."})
    # A workshop must never claim another workshop's platform subdomain.
    reserved = (_root_domain(), "vercel.app")
    if any(hostname == root or hostname.endswith("." + root) for root in reserved):
        raise serializers.ValidationError({"hostname": "This domain is reserved by the platform."})
    return hostname


def _provider(raw):
    provider = str(raw or "other").strip().lower()
    if provider not in {"godaddy", "cloudflare", "hostinger", "namecheap", "vercel", "other"}:
        raise serializers.ValidationError({"dnsProvider": "Choose a supported DNS provider."})
    return provider


def _history(domain):
    return [
        {"action": event.action, "createdAt": event.created_at, "details": event.details}
        for event in domain.events.order_by("-created_at")[:50]
    ]


def _managed_domain(request, domain_id):
    return get_object_or_404(CompanyDomain, pk=domain_id, company=request.user.company)


class CompanyWebsiteDomainsView(APIView):
    permission_classes = [IsCompanyAdmin]

    def get(self, request):
        domains = CompanyDomain.objects.filter(
            company=request.user.company
        ).order_by("-created_at")[:50]
        return Response([_domain_payload(domain) for domain in domains])

    def post(self, request):
        hostname = _validated_hostname(request.data.get("hostname"))
        provider = _provider(request.data.get("dnsProvider"))
        try:
            with transaction.atomic():
                domain = CompanyDomain.objects.create(
                    company=request.user.company, hostname=hostname,
                    dns_provider=provider, status=CompanyDomain.PENDING_SETUP,
                    is_approved=os.environ.get("CUSTOMER_DOMAIN_REQUIRE_APPROVAL", "").lower() != "true",
                )
        except IntegrityError:
            return Response({"message": "This domain is already assigned or pending."}, status=409)
        record_domain_event(domain, request.user, "created", {"provider": provider})
        if not domain.is_approved:
            return Response({
                **_domain_payload(domain),
                "message": "Domain saved. A platform administrator must approve it first.",
            }, status=201)
        try:
            refresh_domain(domain, request.user, attach=True)
        except VercelDomainError as exc:
            domain.last_error = str(exc)
            domain.save(update_fields=["last_error", "updated_at"])
        # Always return the true provider state, including setup failures.
        return Response({
            **_domain_payload(domain),
            "message": "Domain created. Copy the current Vercel DNS records, then verify.",
        }, status=201)


class CompanyWebsiteDomainDetailView(APIView):
    permission_classes = [IsCompanyAdmin]

    def get(self, request, domain_id):
        domain = _managed_domain(request, domain_id)
        return Response({**_domain_payload(domain), "history": _history(domain)})

    def delete(self, request, domain_id):
        domain = _managed_domain(request, domain_id)
        with transaction.atomic():
            domain = CompanyDomain.objects.select_for_update().get(pk=domain.pk)
            domain.status = CompanyDomain.DISABLED
            domain.is_primary = False
            domain.verified_at = None
            domain.ssl_status = "pending"
            domain.save()
            record_domain_event(domain, request.user, "disabled")
        # Fail closed locally even if the Vercel API is unavailable.
        try:
            VercelDomains().remove(domain.hostname)
            domain.last_error = ""
        except VercelDomainError as exc:
            domain.last_error = str(exc)
        domain.save(update_fields=["last_error", "updated_at"])
        record_domain_event(domain, request.user, "removed_from_vercel" if not domain.last_error else "remove_failed")
        return Response({**_domain_payload(domain), "message":
            "Domain disabled; Vercel assignment removed." if not domain.last_error
            else "Domain disabled locally. Vercel removal could not be confirmed; contact the platform administrator."})


class CompanyWebsiteDomainCheckView(APIView):
    permission_classes = [IsCompanyAdmin]

    def post(self, request, domain_id):
        domain = _managed_domain(request, domain_id)
        if domain.status == CompanyDomain.DISABLED or not domain.is_approved:
            return Response({"message": "Domain has been disabled or is awaiting approval."}, status=403)
        try:
            refresh_domain(
                domain, request.user, verify=request.path.endswith("/verify"),
                attach=domain.status == CompanyDomain.PENDING_SETUP,
            )
        except VercelDomainError as exc:
            return Response({
                **_domain_payload(domain), "message": str(exc),
            }, status=503 if exc.code == 503 else 409 if exc.code == 409 else 502)
        return Response(_domain_payload(domain))


class CompanyWebsiteDomainPrimaryView(APIView):
    permission_classes = [IsCompanyAdmin]

    def post(self, request, domain_id):
        domain = _managed_domain(request, domain_id)
        if domain.status != CompanyDomain.ACTIVE or domain.ssl_status != "ready":
            return Response({"message": "Verify ownership, DNS and HTTPS before selecting a primary domain."}, status=409)
        try:
            refresh_domain(domain, request.user)
        except VercelDomainError as exc:
            return Response({"message": str(exc)}, status=502)
        if domain.status != CompanyDomain.ACTIVE:
            return Response({"message": "Domain is not currently HTTPS-ready."}, status=409)
        with transaction.atomic():
            Company.objects.select_for_update().get(pk=request.user.company_id)
            CompanyDomain.objects.filter(company=request.user.company, is_primary=True).update(is_primary=False)
            domain.is_primary = True
            domain.save(update_fields=["is_primary", "updated_at"])
            record_domain_event(domain, request.user, "primary_selected")
        return Response(_domain_payload(domain))


class PlatformCustomerDomainsView(APIView):
    """Super Admin only: never authorized by a workshop-level wildcard role."""
    permission_classes = [IsCompanyAdmin]  # Overridden by get_permissions below.

    def get_permissions(self):
        from rest_framework.permissions import IsAuthenticated
        return [IsAuthenticated()]

    @staticmethod
    def _require_platform(request):
        user = request.user
        if not user.is_authenticated or not user.is_active or not user.is_staff or not user.is_superuser or user.company_id:
            raise PermissionDenied("Platform Super Admin access required.")

    def get(self, request):
        self._require_platform(request)
        domains = CompanyDomain.objects.select_related("company").order_by("-created_at")[:200]
        return Response([
            {"companyId": str(d.company_id), "companyName": d.company.name, **_domain_payload(d)}
            for d in domains
        ])


class PlatformCustomerDomainReviewView(PlatformCustomerDomainsView):
    def post(self, request, domain_id):
        self._require_platform(request)
        action = str(request.data.get("action", "")).lower()
        if action not in {"approve", "disable", "refresh"}:
            return Response({"message": "Choose approve, disable or refresh."}, status=400)
        domain = get_object_or_404(CompanyDomain, pk=domain_id)
        if action == "disable":
            domain.is_approved = False
            domain.status = CompanyDomain.DISABLED
            domain.is_primary = False
            domain.verified_at = None
            domain.ssl_status = "pending"
            domain.save()
            record_domain_event(domain, request.user, "platform_disabled")
        else:
            if action == "approve":
                domain.is_approved = True
                domain.approved_by = request.user
                domain.status = CompanyDomain.PENDING_SETUP
                domain.save()
                record_domain_event(domain, request.user, "platform_approved")
            if not domain.is_approved:
                return Response({"message": "Domain has not been approved."}, status=403)
            try:
                refresh_domain(domain, request.user, attach=action == "approve")
            except VercelDomainError as exc:
                return Response({"message": str(exc), **_domain_payload(domain)}, status=502)
        return Response(_domain_payload(domain))
