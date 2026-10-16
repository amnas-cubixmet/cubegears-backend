"""Owner-only monthly credit statements and grace/overdue status."""
from rest_framework.response import Response
from rest_framework.views import APIView
from apps.accounts.permissions import IsCompanyOwner
from .calendar_billing import billing_status


class CalendarBillingStatusView(APIView):
    permission_classes = [IsCompanyOwner]
    http_method_names = ["get", "head", "options"]

    def get(self, request):
        return Response(billing_status(request.user.company))
