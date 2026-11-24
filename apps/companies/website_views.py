"""Tenant-scoped Customer Website branding and default subdomain management."""
import os
import re

from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsCompanyAdmin
from .models import CompanyWebsiteSettings
from .subdomains import (
    SubdomainUnavailable, check_subdomain_availability, customer_portal_root_domain,
    customer_website_url, ensure_default_subdomain, update_website,
)


def website_address(entry):
    root = customer_portal_root_domain()
    return {
        "subdomainLabel": entry.label,
        "subdomain": f"{entry.label}.{root}",
        "baseDomain": root,
        "websiteUrl": customer_website_url(entry.label),
        "websiteEnabled": entry.website_enabled,
        "wildcardReady": os.environ.get("CUSTOMER_WEBSITE_LINKS_ENABLED", "").lower() == "true",
    }


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


@method_decorator(never_cache, name="dispatch")
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
        settings_data.update(website_address(ensure_default_subdomain(company)))
        settings_data["hasLogo"] = bool(company.logo_image)
        return Response(settings_data)

    def patch(self, request):
        current = self._settings(request)
        serializer = SettingsSerializer(current, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(SettingsSerializer(current).data)


class AddressSerializer(serializers.Serializer):
    label = serializers.CharField(required=False, max_length=63)
    websiteEnabled = serializers.BooleanField(required=False)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("Provide a subdomain or website publication state.")
        return attrs


@method_decorator(never_cache, name="dispatch")
class CompanyWebsiteSubdomainView(APIView):
    """Manage only the signed-in user's company address and publication state."""
    permission_classes = [IsCompanyAdmin]

    def get(self, request):
        try:
            label, available, suggestion = check_subdomain_availability(
                request.user.company, request.query_params.get("label"),
            )
        except ValueError as exc:
            return Response({"label": str(exc)}, status=400)
        return Response({"label": label, "available": available, "suggestedLabel": suggestion})

    def patch(self, request):
        serializer = AddressSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            entry, changed = update_website(
                request.user.company, requested=data.get("label"),
                website_enabled=data.get("websiteEnabled"),
            )
        except SubdomainUnavailable as exc:
            return Response({"label": str(exc), "suggestedLabel": exc.suggestion}, status=400)
        except ValueError as exc:
            return Response({"label": str(exc)}, status=400)
        response = {**website_address(entry), "changed": changed}
        if changed:
            response["message"] = (
                "Address changed. Reissue existing customer links that used the previous hostname."
            )
        return Response(response)
