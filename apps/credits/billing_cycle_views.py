"""Owner-only monthly credit statements and grace/overdue status."""
from rest_framework.response import Response
from rest_framework.views import APIView
from apps.accounts.permissions import IsCompanyOwner
from .calendar_billing import billing_status, finalize_month, settle_statement, active_policy, _period_for
from django.utils import timezone
from .models import StorageMeterPeriod, FeatureMeterPeriod, BillingCycleStatement


class CalendarBillingStatusView(APIView):
    permission_classes = [IsCompanyOwner]
    http_method_names = ["get", "post", "head", "options"]

    def get(self, request):
        return Response(billing_status(request.user.company))

    def post(self, request):
        # Only the account owner can trigger an idempotent, server-priced retry.
        # The request supplies NO amount, company, period or pricing.
        company = request.user.company
        if not active_policy():
            return Response(billing_status(company))
        current = _period_for(timezone.now())
        months = set(StorageMeterPeriod.objects.filter(
            company=company, period__lt=current,
        ).values_list("period", flat=True))
        months.update(FeatureMeterPeriod.objects.filter(
            company=company, period__lt=current,
        ).values_list("period", flat=True))
        months.update(BillingCycleStatement.objects.filter(
            company=company, status="pending",
        ).values_list("month", flat=True))
        for month in sorted(months):
            _, statement = finalize_month(company, month)
            if statement:
                settle_statement(statement)
        return Response(billing_status(company))
