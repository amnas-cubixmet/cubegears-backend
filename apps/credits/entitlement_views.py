"""Company-scoped module visibility and postpaid monthly statements."""
from django.conf import settings
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import user_has_permission
from apps.roles.permission_catalog import TENANT_PERMISSIONS
from .models import MonthlyCreditStatement


CORE_MODULES = frozenset({
    "dashboard", "billing", "company", "settings", "storage",
    "notifications", "roles",
})


class CompanyEntitlementsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not request.user.company_id or not request.user.company.is_active:
            return Response({"message": "Active workshop company required."}, status=403)
        modules = set()
        for permission in TENANT_PERMISSIONS:
            key = permission.partition(".")[0]
            if key in CORE_MODULES or key == "panel":
                continue
            if user_has_permission(request.user, permission):
                modules.add(key)
        return Response({
            "enforced": bool(settings.CREDIT_FEATURE_ENFORCEMENT_ENABLED),
            "modules": sorted(modules),
            "canManageFeatures": user_has_permission(request.user, "billing.edit"),
            "billingMode": (
                "monthly_postpaid" if settings.CREDIT_POSTPAID_BILLING_ENABLED else "prepaid"
            ),
        })


class MonthlyCreditStatementsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not request.user.company_id or not user_has_permission(request.user, "billing.view"):
            return Response({"message": "Company billing access required."}, status=403)
        rows = MonthlyCreditStatement.objects.filter(
            company=request.user.company,
        ).order_by("-period", "-pk")[:24]
        return Response([{
            "id": row.pk,
            "period": row.period,
            "featureCredits": str(row.feature_credits),
            "storageCredits": str(row.storage_credits),
            "totalCredits": str(row.total_credits),
            "status": row.status,
            "details": row.details,
            "createdAt": row.created_at.isoformat(),
            "paidAt": row.paid_at.isoformat() if row.paid_at else None,
        } for row in rows])
