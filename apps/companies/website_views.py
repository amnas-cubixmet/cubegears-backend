"""Workshop-owner website settings. Domain requests remain unverified by design."""
import os
import re
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView
from apps.accounts.permissions import IsCompanyAdmin
from .models import CompanyDomain, CompanyWebsiteSettings


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
        settings_data["subdomain"] = company.slug + "." + os.environ.get(
            "CUSTOMER_PORTAL_ROOT_DOMAIN", "cubegears.com"
        )
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
        "verifiedAt": domain.verified_at,
    }


class CompanyWebsiteDomainsView(APIView):
    permission_classes = [IsCompanyAdmin]

    def get(self, request):
        domains = CompanyDomain.objects.filter(
            company=request.user.company
        ).order_by("-created_at")[:50]
        return Response([_domain_payload(domain) for domain in domains])

    def post(self, request):
        hostname = str(request.data.get("hostname", "")).strip().lower().rstrip(".")
        try:
            hostname = hostname.encode("idna").decode("ascii")
        except (UnicodeError, ValueError):
            return Response({"message": "Invalid domain."}, status=400)
        labels = hostname.split(".")
        if (
            len(hostname) > 253 or len(labels) < 2
            or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part)
                   for part in labels)
            or labels[-1].isdigit()
        ):
            return Response({"message": "Invalid public domain."}, status=400)
        root = os.environ.get("CUSTOMER_PORTAL_ROOT_DOMAIN", "cubegears.com").lower()
        if hostname == root or hostname.endswith("." + root):
            return Response({"message": "CubeGears subdomains are managed by the platform."}, status=400)
        if CompanyDomain.objects.filter(hostname=hostname).exists():
            return Response({"message": "Domain is already assigned or pending."}, status=409)
        try:
            with transaction.atomic():
                domain = CompanyDomain.objects.create(
                    company=request.user.company, hostname=hostname,
                    status=CompanyDomain.PENDING_DNS, is_primary=False,
                )
        except IntegrityError:
            return Response({"message": "Domain already assigned."}, status=409)
        # Do not invent Vercel DNS records or claim verification succeeded.
        return Response({
            **_domain_payload(domain),
            "message": "Domain request saved. DNS ownership, hosting and SSL verification are required before activation.",
        }, status=201)


class CompanyWebsiteDomainDetailView(APIView):
    permission_classes = [IsCompanyAdmin]

    def delete(self, request, domain_id):
        domain = get_object_or_404(
            CompanyDomain, company=request.user.company, pk=domain_id
        )
        domain.status = CompanyDomain.DISABLED
        domain.is_primary = False
        domain.save(update_fields=["status", "is_primary", "updated_at"])
        return Response({"status": "disabled"})
