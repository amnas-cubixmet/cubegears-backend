"""Authoritative tenant navigation permissions for feature-aware clients.

The backend RBAC + paid-feature checks remain authoritative: this endpoint
does not grant or modify permissions and must never be used as an API bypass.
"""
from django.conf import settings
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import user_has_permission
from apps.roles.permission_catalog import TENANT_PERMISSIONS


CORE_MODULES = frozenset({
    "dashboard", "billing", "company", "settings", "storage", "roles",
    "notifications",
})


class TenantFeatureAccessView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        if not user.company_id or not user.company or not user.company.is_active:
            return Response({"message": "An active company account is required."}, status=403)

        permissions = sorted(
            code for code in TENANT_PERMISSIONS
            if user_has_permission(user, code)
        )
        modules = sorted({
            code.partition(".")[0] for code in permissions
            if "." in code and code.partition(".")[0] not in CORE_MODULES
        })
        return Response({
            "companyId": str(user.company_id),
            "permissions": permissions,
            "enabledModules": modules,
            "enforcementEnabled": bool(settings.CREDIT_FEATURE_ENFORCEMENT_ENABLED),
            "strictModulesEnabled": bool(getattr(settings, "CREDIT_FEATURE_STRICT_GATE_ENABLED", False)),
            "canManageFeatures": "billing.edit" in permissions,
        })
