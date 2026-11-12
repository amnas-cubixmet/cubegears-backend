"""Legacy import compatibility; automatic staff delivery is permanently retired.

No Celery tasks or Meta provider calls are registered from this module.
"""
from apps.accounts.models import StaffPasswordInvitation


def staff_auto_delivery_available(employee):
    return False


def queue_staff_whatsapp_invitation(invitation, employee):
    invitation.delivery_status = "manual"
    invitation.failure_reason = ""
    invitation.save(update_fields=["delivery_status", "failure_reason"])
    return "manual"


def send_template_message(*args, **kwargs):
    raise RuntimeError("Automatic WhatsApp messaging is no longer available.")


def send_staff_invitation(invitation_id):
    StaffPasswordInvitation.objects.filter(
        pk=invitation_id, used_at__isnull=True, invalidated_at__isnull=True,
        delivery_status__in=["queued", "sending"]
    ).update(delivery_status="manual", failure_reason="")
    return "manual_only"


# Compatibility with historical tests; this is NOT a registered Celery task.
send_staff_invitation.run = send_staff_invitation
send_staff_invitation.delay = lambda *args, **kwargs: None


def queue_platform_owner_invitation(invitation_id):
    StaffPasswordInvitation.objects.filter(
        pk=invitation_id, used_at__isnull=True, invalidated_at__isnull=True,
    ).update(delivery_status="manual")
    return False
