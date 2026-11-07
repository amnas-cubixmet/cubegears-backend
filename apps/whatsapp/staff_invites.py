"""Opt-in official WhatsApp staff onboarding plus existing platform owner delivery.

No raw setup tokens are stored or passed through Celery. Staff automatic
delivery is disabled by default, and only enabled with consent, an approved
workshop template, an official connected WABA and a configured Redis worker.
"""
from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import StaffPasswordInvitation
from apps.accounts.staff_setup import invitation_url, resolve_invitation
from apps.employees.models import Employee
from .meta import decrypt_token, send_template_message
from .models import WhatsAppBusinessConnection, WhatsAppMessageTemplate
from .phone import normalize_recipient


def staff_auto_delivery_available(employee):
    if not (
        getattr(settings, "STAFF_WHATSAPP_AUTO_SEND_ENABLED", False)
        and settings.WHATSAPP_SEND_ENABLED
        and settings.CELERY_BROKER_URL.startswith(("redis://", "rediss://"))
        and employee and employee.whatsapp_consent
        and employee.whatsapp_consent_source
        and employee.user_id and employee.user.is_active
        and employee.status in {"Active", "Probation", "Notice Period"}
        and employee.user.company_id == employee.company_id
    ):
        return False
    connection = WhatsAppBusinessConnection.objects.filter(
        company_id=employee.company_id, connected=True,
        notifications_enabled=True, messaging_mode="automatic",
    ).first()
    if not connection or not (
        connection.waba_id and connection.phone_number_id
        and connection.encrypted_access_token
        and (connection.event_settings or {}).get("staff_onboarding")
    ):
        return False
    return WhatsAppMessageTemplate.objects.filter(
        company_id=employee.company_id, event="staff_onboarding",
        approval_status="APPROVED",
    ).exclude(template_name="").exists()


def _dispatch_staff_invite(invitation_id):
    try:
        send_staff_invitation.delay(str(invitation_id))
    except Exception:
        # Queue outages must not prevent a workshop from saving its employee.
        StaffPasswordInvitation.objects.filter(
            pk=invitation_id, delivery_status="queued",
        ).update(
            delivery_status="failed",
            failure_reason="WhatsApp worker unavailable; authorized manual sharing remains available.",
        )


def queue_staff_whatsapp_invitation(invitation, employee):
    """Queue once after commit, or retain safe manual sharing as fallback."""
    if invitation.used_at or invitation.invalidated_at:
        return "unavailable"
    if not staff_auto_delivery_available(employee):
        invitation.delivery_status = "manual"
        invitation.failure_reason = ""
        invitation.save(update_fields=["delivery_status", "failure_reason"])
        return "manual"
    invitation.delivery_status = "queued"
    invitation.failure_reason = ""
    invitation.save(update_fields=["delivery_status", "failure_reason"])
    transaction.on_commit(lambda: _dispatch_staff_invite(invitation.pk))
    return "queued"


@shared_task
def send_staff_invitation(invitation_id):
    """At-most-once dispatch per invitation; uncertain sends are not retried.

    A provider/network timeout may have accepted the message. Mark failure for
    authorized review instead of accidentally sending the same link twice.
    """
    if not getattr(settings, "STAFF_WHATSAPP_AUTO_SEND_ENABLED", False):
        StaffPasswordInvitation.objects.filter(
            pk=invitation_id,
            delivery_status__in=["queued", "sending"],
            used_at__isnull=True, invalidated_at__isnull=True,
        ).update(
            delivery_status="manual",
            failure_reason="Staff automatic delivery is disabled; share manually.",
        )
        return "manual_only"

    with transaction.atomic():
        invite = StaffPasswordInvitation.objects.select_for_update().select_related(
            "user", "user__company", "user__role",
        ).filter(pk=invitation_id).first()
        if not invite:
            return "unavailable"
        if invite.delivery_status != "queued":
            return invite.delivery_status
        employee = Employee.objects.select_related("user").filter(
            user_id=invite.user_id, company_id=invite.user.company_id,
        ).first()
        if not employee or not staff_auto_delivery_available(employee):
            invite.delivery_status = "manual"
            invite.failure_reason = "Automatic WhatsApp requirements no longer met."
            invite.save(update_fields=["delivery_status", "failure_reason"])
            return "manual"
        if not resolve_invitation(invitation_url(invite).rsplit("/", 1)[-1]):
            invite.delivery_status = "failed"
            invite.failure_reason = "Invitation expired, was consumed or was revoked."
            invite.save(update_fields=["delivery_status", "failure_reason"])
            return "invalid"

        invite.delivery_status = "sending"
        invite.attempts += 1
        invite.save(update_fields=["delivery_status", "attempts"])

    # No database lock is held across Meta's external HTTP call.
    try:
        connection = WhatsAppBusinessConnection.objects.get(company_id=invite.user.company_id)
        template = WhatsAppMessageTemplate.objects.get(
            company_id=invite.user.company_id, event="staff_onboarding",
            approval_status="APPROVED",
        )
        destination = normalize_recipient(invite.user.normalized_phone)
        if not destination:
            raise ValueError("Missing valid recipient")
        if not employee.whatsapp_consent:
            raise ValueError("Staff consent revoked")
        result = send_template_message(
            connection.phone_number_id,
            decrypt_token(connection.encrypted_access_token),
            destination, template.template_name, template.language,
            invite.user.name, invite.user.company.name, invitation_url(invite),
        )
        message_id = (result.get("messages") or [{}])[0].get("id")
        if not message_id:
            raise ValueError("Meta did not return a message ID")
        StaffPasswordInvitation.objects.filter(
            pk=invite.pk, delivery_status="sending",
        ).update(delivery_status="sent", meta_message_id=str(message_id), failure_reason="")
        return "sent"
    except Exception:
        StaffPasswordInvitation.objects.filter(
            pk=invite.pk, delivery_status="sending",
        ).update(
            delivery_status="failed",
            failure_reason="Official WhatsApp delivery could not be verified. Do not retry automatically.",
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
