"""Official WhatsApp staff setup delivery using each workshop's existing WABA.

Raw password-link tokens never enter DB storage, Celery task arguments or logs.
"""
from celery import shared_task
from django.conf import settings
from apps.accounts.models import StaffPasswordInvitation
from apps.accounts.staff_setup import invitation_url
from .meta import send_template_message
from .phone import normalize_recipient



def staff_auto_delivery_available(_employee):
    """Staff delivery is manual-only. Keep callable for legacy integrations."""
    return False


def queue_staff_whatsapp_invitation(invitation, _employee):
    """Compatibility: never dispatch WhatsApp, email or Celery for staff."""
    if invitation.used_at or invitation.invalidated_at:
        return "unavailable"
    invitation.delivery_status = "manual"
    invitation.failure_reason = ""
    invitation.save(update_fields=["delivery_status", "failure_reason"])
    return "manual"


@shared_task
def send_staff_invitation(invitation_id):
    """Drain obsolete queued jobs without calling Meta.

    A Celery task from a previous deployment could still be pending when
    the manual-only onboarding rollout completes. Make the worker inert.
    Workshop-owner registration uses its separate platform sender below.
    """
    StaffPasswordInvitation.objects.filter(
        pk=invitation_id,
        delivery_status__in=["queued", "sending"],
        used_at__isnull=True,
        invalidated_at__isnull=True,
    ).update(
        delivery_status="manual",
        failure_reason="Staff invitation requires manual sharing by an authorized administrator.",
    )
    return "manual_only"


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
