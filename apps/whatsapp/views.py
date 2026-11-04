"""Tenant-owned Meta WhatsApp Business onboarding, controls, consent, and logs."""
import re
import uuid
from django.conf import settings
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from apps.accounts.permissions import user_has_permission
from apps.customers.models import Customer
from .meta import (
    configured, encrypt_token, decrypt_token, exchange_embedded_signup_code,
    verify_waba_phone, approved_templates,
)
from .models import (
    WhatsAppBusinessConnection, WhatsAppMessageTemplate,
    WhatsAppNotificationLog, CustomerNotificationPreference,
)
from .tasks import EVENTS, queue_customer_notification


def _manage(request):
    if not (user_has_permission(request.user, "settings.manage")
            or user_has_permission(request.user, "company.manage")):
        from rest_framework.exceptions import PermissionDenied
        raise PermissionDenied("Workshop administrator permission is required.")


class WhatsAppSettingsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        _manage(request)
        connection = WhatsAppBusinessConnection.objects.filter(company=request.user.company).first()
        templates = WhatsAppMessageTemplate.objects.filter(company=request.user.company).order_by("event")
        return Response({
            "configured": configured() and bool(settings.WHATSAPP_META_CONFIG_ID),
            "appId": settings.WHATSAPP_META_APP_ID if configured() else "",
            "configId": settings.WHATSAPP_META_CONFIG_ID if configured() else "",
            "connected": bool(connection and connection.connected),
            "phone": connection.display_phone_number if connection and connection.connected else "",
            "wabaId": connection.waba_id if connection and connection.connected else "",
            "notificationsEnabled": bool(connection and connection.notifications_enabled),
            "sendEnabled": bool(settings.WHATSAPP_SEND_ENABLED and settings.CELERY_BROKER_URL),
            "events": connection.event_settings if connection else {},
            "eventNames": EVENTS,
            "templates": [
                {"event": t.event, "name": t.template_name,
                 "language": t.language, "status": t.approval_status}
                for t in templates
            ],
        })

    def patch(self, request):
        _manage(request)
        connection = get_object_or_404(WhatsAppBusinessConnection, company=request.user.company)
        if not connection.connected:
            return Response({"message": "Connect a verified WhatsApp Business account first."}, status=409)
        choices = request.data.get("events")
        enabled = request.data.get("notificationsEnabled")
        if choices is not None:
            if not isinstance(choices, dict) or any(
                key not in EVENTS or not isinstance(value, bool)
                for key, value in choices.items()
            ):
                return Response({"message": "Invalid notification settings."}, status=400)
            connection.event_settings = choices
        if enabled is not None:
            if not isinstance(enabled, bool):
                return Response({"message": "notificationsEnabled must be a boolean."}, status=400)
            connection.notifications_enabled = enabled
        connection.save(update_fields=["event_settings", "notifications_enabled", "updated_at"])
        return self.get(request)


class WhatsAppConnectView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        _manage(request)
        code = str(request.data.get("code") or "")
        waba = str(request.data.get("wabaId") or "")
        phone_id = str(request.data.get("phoneNumberId") or "")
        if not configured() or not settings.WHATSAPP_META_CONFIG_ID:
            return Response({"message": "Meta Embedded Signup is not configured for this installation."}, status=503)
        if not 8 <= len(code) <= 2000 or not re.fullmatch(r"\d{6,40}", waba) or not re.fullmatch(r"\d{6,40}", phone_id):
            return Response({"message": "Invalid official Meta onboarding response."}, status=400)
        try:
            token_response = exchange_embedded_signup_code(code)
            access_token = token_response.get("access_token")
            if not access_token:
                raise ValueError("Meta did not return an access token.")
            display = verify_waba_phone(waba, phone_id, access_token)
            encrypted = encrypt_token(access_token)
        except ValueError as exc:
            return Response({"message": str(exc)}, status=400)
        if WhatsAppBusinessConnection.objects.exclude(company=request.user.company).filter(
            phone_number_id=phone_id, connected=True,
        ).exists():
            return Response({"message": "This WhatsApp business phone is already connected to another workshop."}, status=409)
        connection, _ = WhatsAppBusinessConnection.objects.update_or_create(
            company=request.user.company,
            defaults={
                "waba_id": waba, "phone_number_id": phone_id,
                "display_phone_number": display, "encrypted_access_token": encrypted,
                "connected": True, "connected_at": timezone.now(),
                "notifications_enabled": False, "event_settings": {},
            },
        )
        return Response({"connected": True, "phone": connection.display_phone_number})


class WhatsAppDisconnectView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        _manage(request)
        WhatsAppBusinessConnection.objects.filter(company=request.user.company).update(
            connected=False, notifications_enabled=False, encrypted_access_token="",
        )
        return Response({"connected": False})


class WhatsAppTemplateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        _manage(request)
        connection = get_object_or_404(WhatsAppBusinessConnection, company=request.user.company, connected=True)
        event = str(request.data.get("event") or "")
        name = str(request.data.get("name") or "").strip()
        language = str(request.data.get("language") or "en_US")
        if event not in EVENTS or not re.fullmatch(r"[a-z0-9_]{1,180}", name) or not re.fullmatch(r"[a-z]{2}(?:_[A-Z]{2})?", language):
            return Response({"message": "Choose a valid event, approved Meta template name, and language."}, status=400)
        try:
            available = approved_templates(connection.waba_id, decrypt_token(connection.encrypted_access_token))
        except ValueError as exc:
            return Response({"message":str(exc)}, status=400)
        if not any(t["name"] == name and t["language"] == language for t in available):
            return Response({"message":"This exact language/template is not approved for this WhatsApp Business Account."}, status=400)
        WhatsAppMessageTemplate.objects.update_or_create(
            company=request.user.company, event=event,
            defaults={
                "branch":request.user.branch, "template_name":name,
                "language":language, "approval_status":"APPROVED",
            },
        )
        return Response({"event":event, "name":name, "language":language, "status":"APPROVED"})


class WhatsAppLogsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        _manage(request)
        logs = WhatsAppNotificationLog.objects.filter(company=request.user.company).order_by("-created_at")[:100]
        return Response([{
            "id":str(log.id), "event":log.event, "status":log.status,
            "phone":log.phone[-4:].rjust(len(log.phone), "*"),
            "messageId":log.meta_message_id, "attempts":log.attempt_count,
            "error":log.error_detail, "createdAt":log.created_at,
        } for log in logs])


class WhatsAppCustomerPreferenceView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, customer_id):
        _manage(request)
        customer = get_object_or_404(Customer, pk=customer_id, company=request.user.company)
        consent = request.data.get("optIn")
        source = str(request.data.get("source") or "").strip()
        if not isinstance(consent, bool) or (consent and (len(source) < 3 or len(source) > 120)):
            return Response({"message": "Explicit consent and a recorded consent source are required."}, status=400)
        preference, _ = CustomerNotificationPreference.objects.get_or_create(
            customer=customer,
            defaults={"company":customer.company, "branch":customer.branch},
        )
        preference.whatsapp_opt_in = consent
        preference.consent_source = source if consent else ""
        preference.opted_in_at = timezone.now() if consent else None
        preference.opted_out_at = None if consent else timezone.now()
        preference.save()
        return Response({"optIn":consent})


class WhatsAppTestMessageView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        _manage(request)
        customer = get_object_or_404(Customer, pk=request.data.get("customerId"), company=request.user.company)
        log = queue_customer_notification(
            customer, "test", f"test:{uuid.uuid4().hex}",
        )
        if not log:
            return Response({"message":"Connect WhatsApp, enable test events, configure an approved template and record customer opt-in."}, status=409)
        return Response({"queued":True, "id":str(log.id), "sendingEnabled":bool(settings.WHATSAPP_SEND_ENABLED and settings.CELERY_BROKER_URL)}, status=202)
