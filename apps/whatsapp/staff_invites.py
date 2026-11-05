"""Official WhatsApp staff setup delivery using each workshop's existing WABA.

Raw password-link tokens never enter DB storage, Celery task arguments or logs.
"""
from celery import shared_task
from django.conf import settings
from django.db import transaction
from apps.accounts.models import StaffPasswordInvitation
from apps.accounts.staff_setup import invitation_url
from apps.employees.models import Employee
from .meta import approved_templates, decrypt_token, send_template_message
from .phone import normalize_recipient
from .models import WhatsAppBusinessConnection, WhatsAppMessageTemplate


def queue_staff_whatsapp_invitation(invitation, employee):
    if (not employee.whatsapp_consent or not employee.whatsapp_consent_source
            or not employee.phone or not employee.user_id):
        invitation.delivery_status = "manual"
        invitation.failure_reason = "WhatsApp consent not recorded; manual secure delivery required."
        invitation.save(update_fields=["delivery_status", "failure_reason"])
        return "manual"
    connection = WhatsAppBusinessConnection.objects.filter(
        company=employee.company, connected=True, notifications_enabled=True,
    ).first()
    template = WhatsAppMessageTemplate.objects.filter(
        company=employee.company, event="staff_onboarding", approval_status="APPROVED",
    ).first()
    if not connection or connection.messaging_mode != "automatic" or not connection.event_settings.get("staff_onboarding", False) or not template:
        invitation.delivery_status = "manual"
        invitation.failure_reason = "Workshop WhatsApp disconnected or staff template not enabled."
        invitation.save(update_fields=["delivery_status", "failure_reason"])
        return "manual"
    if not settings.WHATSAPP_SEND_ENABLED or not settings.CELERY_BROKER_URL:
        invitation.delivery_status = "manual"
        invitation.failure_reason = "Workshop WhatsApp sender and Celery worker are disabled."
        invitation.save(update_fields=["delivery_status", "failure_reason"])
        return "manual"
    invitation.delivery_status = "queued"
    invitation.failure_reason = ""
    invitation.save(update_fields=["delivery_status", "failure_reason"])
    pk = str(invitation.pk)
    def schedule():
        try:
            send_staff_invitation.delay(pk)
        except Exception:
            StaffPasswordInvitation.objects.filter(pk=pk, delivery_status="queued").update(
                delivery_status="failed", failure_reason="WhatsApp queue unavailable. Resend or share a new secure link.",
            )
    transaction.on_commit(schedule)
    return "queued"


@shared_task
def send_staff_invitation(invitation_id):
    if not settings.WHATSAPP_SEND_ENABLED:
        return "disabled"
    with transaction.atomic():
        invite = StaffPasswordInvitation.objects.select_for_update().select_related("user__company").get(pk=invitation_id)
        if invite.delivery_status != "queued" or invite.used_at or invite.invalidated_at or invite.expires_at <= __import__("django.utils.timezone", fromlist=["now"]).now():
            return "skipped"
        user = invite.user
        employee = Employee.objects.filter(
            company=user.company, user=user, whatsapp_consent=True,
        ).first()
        connection = WhatsAppBusinessConnection.objects.filter(
            company=user.company, connected=True, notifications_enabled=True,
        ).first()
        template = WhatsAppMessageTemplate.objects.filter(
            company=user.company, event="staff_onboarding", approval_status="APPROVED",
        ).first()
        if (not user.is_active or not employee or not connection or connection.messaging_mode != "automatic" or not template
                or not connection.event_settings.get("staff_onboarding", False)):
            invite.delivery_status = "failed"
            invite.failure_reason = "Staff, consent or WhatsApp connection is unavailable."
            invite.save(update_fields=["delivery_status", "failure_reason"])
            return "failed"
        recipient = normalize_recipient(user.normalized_phone)
        if not recipient:
            invite.delivery_status = "failed"
            invite.failure_reason = "Valid international mobile number required."
            invite.save(update_fields=["delivery_status", "failure_reason"])
            return "failed"
        invite.delivery_status = "sending"
        invite.attempts += 1
        invite.save(update_fields=["delivery_status", "attempts"])

    try:
        token = decrypt_token(connection.encrypted_access_token)
        if not any(
            row.get("name") == template.template_name and row.get("language") == template.language
            for row in approved_templates(connection.waba_id, token)
        ):
            raise ValueError("Staff invitation template approval is no longer valid.")
        result = send_template_message(
            connection.phone_number_id,
            token,
            recipient, template.template_name, template.language,
            employee.name, user.company.name, invitation_url(invite),
        )
        msg_id = (result.get("messages") or [{}])[0].get("id")
        if not msg_id:
            raise ValueError("Meta did not return a message ID.")
        StaffPasswordInvitation.objects.filter(pk=invite.pk, delivery_status="sending").update(
            delivery_status="sent", meta_message_id=msg_id, failure_reason="",
        )
        return "sent"
    except Exception:
        # Meta might have accepted a message before the network failed.
        # No blind task retries: this protects customers from duplicate links.
        StaffPasswordInvitation.objects.filter(pk=invite.pk, delivery_status="sending").update(
            delivery_status="failed",
            failure_reason="Official Meta delivery not confirmed. Resend a new setup link.",
        )
        return "failed"


@shared_task
def send_platform_owner_invitation(invitation_id):
    """Platform sender is separate from the new workshop's not-yet-connected WABA."""
    invite = StaffPasswordInvitation.objects.select_related("user__company").filter(pk=invitation_id).first()
    if not invite or invite.used_at or invite.invalidated_at:
        return "unavailable"
    if not (settings.WHATSAPP_SEND_ENABLED and settings.WHATSAPP_PLATFORM_PHONE_ID
            and settings.WHATSAPP_PLATFORM_ACCESS_TOKEN and settings.WHATSAPP_PLATFORM_TEMPLATE):
        StaffPasswordInvitation.objects.filter(pk=invitation_id).update(
            delivery_status="failed",
            failure_reason="Platform welcome message sender not configured. Account activation pending.",
        )
        return "not-configured"
    if not settings.CELERY_BROKER_URL:
        return "disabled"
    user = invite.user
    digits = normalize_recipient(user.normalized_phone)
    if not digits:
        return "bad-number"
    try:
        result = send_template_message(
            settings.WHATSAPP_PLATFORM_PHONE_ID, settings.WHATSAPP_PLATFORM_ACCESS_TOKEN,
            digits, settings.WHATSAPP_PLATFORM_TEMPLATE, settings.WHATSAPP_PLATFORM_TEMPLATE_LANGUAGE,
            user.name, user.company.name, invitation_url(invite),
        )
        msg_id = (result.get("messages") or [{}])[0].get("id")
        if not msg_id:
            raise ValueError("No Meta message ID.")
        StaffPasswordInvitation.objects.filter(pk=invite.pk).update(
            delivery_status="sent",meta_message_id=str(msg_id),failure_reason="",
        )
        return "sent"
    except Exception:
        StaffPasswordInvitation.objects.filter(pk=invite.pk).update(
            delivery_status="failed",failure_reason="Official platform WhatsApp delivery could not be verified.",
        )
        return "failed"


def queue_platform_owner_invitation(invitation_id):
    """Called on transaction commit. No passwords/links in task args."""
    from apps.accounts.models import StaffPasswordInvitation
    if not (settings.WHATSAPP_SEND_ENABLED and settings.CELERY_BROKER_URL
            and settings.WHATSAPP_PLATFORM_PHONE_ID and settings.WHATSAPP_PLATFORM_ACCESS_TOKEN
            and settings.WHATSAPP_PLATFORM_TEMPLATE):
        StaffPasswordInvitation.objects.filter(pk=invitation_id).update(
            delivery_status="failed", failure_reason="Platform onboarding delivery not configured.",
        )
        return False
    try:
        send_platform_owner_invitation.delay(str(invitation_id))
        StaffPasswordInvitation.objects.filter(pk=invitation_id).update(delivery_status="queued")
        return True
    except Exception:
        StaffPasswordInvitation.objects.filter(pk=invitation_id).update(
            delivery_status="failed", failure_reason="Platform invitation queue unavailable.",
        )
        return False
