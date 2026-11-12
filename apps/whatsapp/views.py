"""Workshop-scoped manual sharing preferences and historical log access."""
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import user_has_permission
from apps.customers.models import Customer
from .models import CustomerNotificationPreference, WhatsAppNotificationLog


def _manage(request):
    if not (user_has_permission(request.user, "settings.manage")
            or user_has_permission(request.user, "company.manage")):
        raise PermissionDenied("Workshop administrator permission is required.")


class WhatsAppSettingsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        _manage(request)
        return Response({
            "messagingMode": "manual",
            "configured": True,
            "connected": False,
            "notificationsEnabled": False,
            "sendEnabled": False,
            "eventNames": [],
            "events": {},
            "templates": [],
        })

    def patch(self, request):
        _manage(request)
        if request.data.get("messagingMode", "manual") != "manual" or request.data.get("notificationsEnabled", False):
            return Response({"message": "Only manual WhatsApp sharing is supported."}, status=400)
        return self.get(request)


class WhatsAppLogsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        _manage(request)
        logs = WhatsAppNotificationLog.objects.filter(company=request.user.company).order_by("-created_at")[:100]
        return Response([{
            "id": str(log.id), "event": log.event, "status": log.status,
            "phone": log.phone[-4:].rjust(len(log.phone), "*"),
            "createdAt": log.created_at,
        } for log in logs])


class WhatsAppCustomerPreferenceView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, customer_id):
        _manage(request)
        customer = get_object_or_404(Customer, pk=customer_id, company=request.user.company)
        pref = CustomerNotificationPreference.objects.filter(customer=customer, company=customer.company).first()
        return Response({"optIn": bool(pref and pref.whatsapp_opt_in),
                         "source": pref.consent_source if pref and pref.whatsapp_opt_in else ""})

    def post(self, request, customer_id):
        _manage(request)
        customer = get_object_or_404(Customer, pk=customer_id, company=request.user.company)
        opted_in = request.data.get("optIn")
        source = str(request.data.get("source") or "").strip()
        if not isinstance(opted_in, bool) or (opted_in and not 3 <= len(source) <= 120):
            return Response({"message": "Consent must be a boolean with a recorded source."}, status=400)
        pref, _ = CustomerNotificationPreference.objects.get_or_create(
            customer=customer, defaults={"company": customer.company, "branch": customer.branch}
        )
        pref.whatsapp_opt_in = opted_in
        pref.consent_source = source if opted_in else ""
        pref.opted_in_at = timezone.now() if opted_in else None
        pref.opted_out_at = None if opted_in else timezone.now()
        pref.save()
        return Response({"optIn": opted_in})
