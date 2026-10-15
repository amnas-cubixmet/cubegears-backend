"""Read-only effective company module visibility, never a browser authority."""
from django.conf import settings
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import user_has_permission
from apps.roles.permission_catalog import TENANT_PERMISSIONS
from apps.saas.models import PlanFeature
from apps.saas.access import active_label_modules

_CORE = frozenset({"dashboard", "billing", "company", "settings",
                   "storage", "notifications", "roles"})


class CompanyModuleEntitlementsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not request.user.company_id or not request.user.is_active:
            return Response({"message": "Workshop account required."}, status=403)
        label_only = bool(getattr(settings, "CREDIT_LABEL_ONLY_ACCESS_ENABLED", False))
        enforced = bool(label_only or (
            settings.CREDIT_FEATURE_ENFORCEMENT_ENABLED and
            settings.CREDIT_STRICT_MODULE_GATING_ENABLED
        ))
        module_actions = {}
        for code in TENANT_PERMISSIONS:
            module, _, action = code.partition(".")
            if module not in _CORE and action:
                module_actions.setdefault(module, []).append(code)
        # Once strict mode is enabled, the same user_has_permission() that
        # protects DRF endpoints determines feature visibility per staff role.
        available = sorted(
            module for module, actions in module_actions.items()
            if any(user_has_permission(request.user, action) for action in actions)
        )
        registered = set(PlanFeature.objects.filter(is_active=True).values_list("key", flat=True))
        return Response({
            "enforced": enforced,
            "labelOnly": label_only,
            "activeLabelModules": sorted(active_label_modules(request.user.company)) if label_only else [],
            "modules": available,
            "registeredModules": sorted(registered),
            "canManageFeatures": user_has_permission(request.user, "billing.edit"),
            "moduleCount": len(available),
        })
