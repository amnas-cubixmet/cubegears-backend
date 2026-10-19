"""Read-only cycle invoices and explicit owner-initiated wallet settlement retry."""
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsCompanyOwner
from .cycle_billing import cycle_status


class CreditCycleStatusView(APIView):
    permission_classes = [IsCompanyOwner]
    http_method_names = ["get", "post", "head", "options"]

    def get(self, request):
        return Response(cycle_status(request.user.company))

    def post(self, request):
        # Re-attempt the company's due invoice using trusted backend rows,
        # never a browser-provided amount. Do not run full tenant settlement
        # from a public request.
        from .cycle_billing import settle_cycle_category, effective_policy, cycle_bounds, _advance_month, _zone
        from django.utils import timezone
        from datetime import datetime
        policy = effective_policy()
        if not policy:
            return Response({"message": "Cycle billing is disabled."}, status=409)
        now = timezone.now()
        open_start, _ = cycle_bounds(now, policy.day_of_month)
        # Past due only. Closed cycles are audited by invoice rows, not
        # browser-submitted period identifiers.
        results = {}
        from .models import CreditCycleInvoice
        pending = CreditCycleInvoice.objects.filter(
            company=request.user.company, status__in=("pending", "insufficient"),
            due_at__lte=now,
        ).order_by("starts_on", "category")
        for invoice in pending:
            y, m = invoice.starts_on.year, invoice.starts_on.month
            start = datetime(y, m, policy.day_of_month, tzinfo=_zone())
            y2, m2 = _advance_month(y, m)
            end = datetime(y2, m2, policy.day_of_month, tzinfo=_zone())
            state, _ = settle_cycle_category(request.user.company, invoice.category, start, end, at=now)
            results[state] = results.get(state, 0) + 1
        return Response({"attempted": results, **cycle_status(request.user.company)})
