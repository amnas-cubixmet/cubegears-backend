"""Meta-signed Cloud API webhooks; delivery and explicit STOP opt-outs."""
import hashlib
import hmac
import json
import re
from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView
from .models import (
    WhatsAppBusinessConnection, WhatsAppNotificationLog,
    CustomerNotificationPreference,
)
from apps.customers.models import Customer
from apps.accounts.models import StaffPasswordInvitation


def _digits(value):
    return re.sub(r"\D", "", str(value or ""))


class MetaWhatsAppWebhookView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        expected = getattr(settings, "WHATSAPP_WEBHOOK_VERIFY_TOKEN", "")
        value = request.query_params.get("hub.verify_token", "")
        mode = request.query_params.get("hub.mode", "")
        challenge = request.query_params.get("hub.challenge", "")
        if expected and mode == "subscribe" and hmac.compare_digest(value, expected):
            return HttpResponse(challenge, content_type="text/plain")
        return HttpResponse(status=403)

    def post(self, request):
        secret = getattr(settings, "WHATSAPP_META_APP_SECRET", "")
        signature = request.headers.get("X-Hub-Signature-256", "")
        if not secret or not signature.startswith("sha256="):
            return HttpResponse(status=403)
        actual = hmac.new(secret.encode(), request.body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature[7:], actual):
            return HttpResponse(status=403)
        try:
            body = json.loads(request.body.decode())
        except (ValueError, UnicodeError):
            return HttpResponse(status=400)
        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value") or {}
                meta_phone = str((value.get("metadata") or {}).get("phone_number_id") or "")
                connection = WhatsAppBusinessConnection.objects.filter(
                    phone_number_id=meta_phone, connected=True,
                ).first()
                if not connection:
                    continue
                for status_event in value.get("statuses") or []:
                    msg_id = str(status_event.get("id") or "")
                    status = str(status_event.get("status") or "")
                    if status not in {"sent", "delivered", "read", "failed"}:
                        continue
                    update = {"status":status}
                    if status == "delivered":
                        update["delivered_at"] = timezone.now()
                    if status == "failed":
                        update["error_detail"] = "Meta webhook reported failed delivery."
                    # Signed status webhooks can be out of order; never move read/delivered back to sent.
                    allowed_prior = {
                        "sent": ["pending", "queued", "sending", "sent"],
                        "delivered": ["pending", "queued", "sending", "sent", "delivered", "failed"],
                        "read": ["pending", "queued", "sending", "sent", "delivered", "read", "failed"],
                        "failed": ["pending", "queued", "sending", "sent", "failed"],
                    }[status]
                    WhatsAppNotificationLog.objects.filter(
                        company=connection.company, meta_message_id=msg_id,
                        status__in=allowed_prior,
                    ).update(**update)
                    invite_updates = {"delivery_status": status}
                    if status == "failed":
                        invite_updates["failure_reason"] = "Meta reported staff invitation delivery failure."
                    StaffPasswordInvitation.objects.filter(
                        user__company=connection.company, meta_message_id=msg_id,
                        used_at__isnull=True, invalidated_at__isnull=True,
                        delivery_status__in=allowed_prior,
                    ).update(**invite_updates)
                for message in value.get("messages") or []:
                    body_text = str(((message.get("text") or {}).get("body") or "")).strip().upper()
                    if body_text not in {"STOP", "UNSUBSCRIBE", "OPT OUT"}:
                        continue
                    sender = _digits(message.get("from"))
                    if not sender:
                        continue
                    for customer in Customer.objects.filter(company=connection.company).only(
                        "id", "company_id", "branch_id", "phone", "whatsapp"
                    )[:2000]:
                        if sender not in {_digits(customer.whatsapp), _digits(customer.phone)}:
                            continue
                        preference, _ = CustomerNotificationPreference.objects.get_or_create(
                            customer=customer,
                            defaults={"company":connection.company, "branch":customer.branch},
                        )
                        preference.whatsapp_opt_in = False
                        preference.opted_out_at = timezone.now()
                        preference.save(update_fields=["whatsapp_opt_in","opted_out_at","updated_at"])
        return HttpResponse(status=200)
