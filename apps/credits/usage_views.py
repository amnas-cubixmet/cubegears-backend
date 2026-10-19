"""Read-only, company-scoped monthly credit usage and settlement history."""
from django.db.models import Sum
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import user_has_permission, IsCompanyOwner
from .models import MonthlyUsageInvoice, StorageUsageInterval


class CompanyUsageInvoicesView(APIView):
    permission_classes = [IsCompanyOwner]

    def get(self, request):
        if not request.user.company_id or not user_has_permission(request.user, "billing.view"):
            return Response({"message": "Company billing view permission required."}, status=403)
        company = request.user.company
        invoices = MonthlyUsageInvoice.objects.filter(company=company).order_by(
            "-billing_month", "category",
        )[:24]
        month = timezone.localdate().strftime("%Y-%m")
        usage = StorageUsageInterval.objects.filter(
            company=company, billing_month=month,
        )
        return Response({
            "currentMonth": month,
            "meteredStorageSamples": usage.count(),
            "storageAccruedCredits": str(usage.aggregate(total=Sum("credits_accrued"))["total"] or 0),
            "invoices": [{
                "id": row.pk,
                "month": row.billing_month,
                "category": row.category,
                "creditsDue": str(row.credits_due),
                "status": row.status,
                "settledAt": row.settled_at.isoformat() if row.settled_at else None,
            } for row in invoices],
        })
