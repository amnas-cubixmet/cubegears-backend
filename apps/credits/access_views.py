"""Authoritative tenant navigation permissions for feature-aware clients.

The backend RBAC + paid-feature checks remain authoritative: this endpoint
does not grant or modify permissions and must never be used as an API bypass.
"""
from django.conf import settings
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from django.utils import timezone
from apps.credits.models import CompanyBundleAccess
from apps.saas.access import active_label_modules
from apps.accounts.permissions import user_has_permission, is_company_owner
from apps.roles.permission_catalog import TENANT_PERMISSIONS


CORE_MODULES = frozenset({
    "billing", "company", "roles",
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
        core = CORE_MODULES if user.company.require_paid_features else CORE_MODULES | {"dashboard", "settings", "storage", "notifications"}
        modules = sorted({
            code.partition(".")[0] for code in permissions
            if "." in code and code.partition(".")[0] not in core
        })
        label_only = bool(getattr(settings, "CREDIT_LABEL_ONLY_ACCESS_ENABLED", False))
        label_modules = sorted(active_label_modules(user.company)) if label_only else []
        next_expiry = None
        if label_only:
            next_expiry = CompanyBundleAccess.objects.filter(
                company=user.company, enabled=True, bundle__is_active=True,
                valid_until__gt=timezone.now(),
            ).exclude(billing_type_at_purchase="per_use").order_by(
                "valid_until"
            ).values_list("valid_until", flat=True).first()
        return Response({
            "companyId": str(user.company_id),
            "isCompanyOwner": is_company_owner(user),
            "paidAccessRequired": user.company.require_paid_features,
            "permissions": permissions,
            "enabledModules": modules,
            "labelOnly": label_only,
            "activeLabelModules": label_modules,
            "nextLabelExpiryAt": next_expiry.isoformat() if next_expiry else None,
            "enforcementEnabled": bool(
                user.company.require_paid_features or settings.CREDIT_FEATURE_ENFORCEMENT_ENABLED or label_only
            ),
            "strictModulesEnabled": bool(getattr(settings, "CREDIT_FEATURE_STRICT_GATE_ENABLED", False)),
            "canManageFeatures": "billing.edit" in permissions,
        })
