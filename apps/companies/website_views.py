"""Tenant-scoped Customer Website branding and default subdomain management."""
import os
import re

from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsCompanyAdmin
from .models import CompanyWebsiteSettings
from .subdomains import (
    assign_subdomain, ensure_default_subdomain,
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
        # A website hostname has its own global reservation and is never the
        # mutable company name nor the staff-login slug.
        label = ensure_default_subdomain(company).label
        settings_data["subdomain"] = label + "." + _root_domain()
        settings_data["subdomainLabel"] = label
        settings_data["wildcardReady"] = os.environ.get(
            "CUSTOMER_WEBSITE_LINKS_ENABLED", ""
        ).lower() == "true"
        settings_data["hasLogo"] = bool(company.logo_image)
        return Response(settings_data)

    def patch(self, request):
        current = self._settings(request)
        serializer = SettingsSerializer(current, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(SettingsSerializer(current).data)



class CompanyWebsiteSubdomainView(APIView):
    """Workshop owner edits only their public address, not the auth slug."""
    permission_classes = [IsCompanyAdmin]

    def patch(self, request):
        requested = request.data.get("label")
        try:
            previous = ensure_default_subdomain(request.user.company).label
            entry = assign_subdomain(request.user.company, requested)
        except ValueError as exc:
            return Response({"label": str(exc)}, status=400)
        return Response({
            "subdomainLabel": entry.label,
            "subdomain": entry.label + "." + _root_domain(),
            "changed": previous != entry.label,
            "wildcardReady": os.environ.get(
                "CUSTOMER_WEBSITE_LINKS_ENABLED", ""
            ).lower() == "true",
            "message": (
                "Address changed. Reissue existing customer links that used the previous hostname."
                if previous != entry.label else "Address is unchanged."
            ),
        })
