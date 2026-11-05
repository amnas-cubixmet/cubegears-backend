"""Idempotent, consent-gated WhatsApp delivery using existing Celery worker."""
from celery import shared_task
from django.conf import settings
from django.db import transaction
from apps.customers.portal_tokens import ensure_portal_access, issue_link
from .meta import decrypt_token, send_template_message
from .phone import normalize_recipient
from .models import (
    WhatsAppBusinessConnection, WhatsAppMessageTemplate,
    CustomerNotificationPreference, WhatsAppNotificationLog,
)

EVENTS = (
    "tracking_link", "staff_onboarding", "job_created", "inspection_completed",
    "work_started", "status_changed", "work_completed",
    "ready_delivery", "invoice_published", "test",
)


def _phone(customer):
    return normalize_recipient(customer.whatsapp or customer.phone)


def queue_customer_notification(customer, event, event_key, job=None):
    """Create at most one per-event record; never send without explicit opt-in."""
    if event not in EVENTS or not customer or not customer.company_id:
        return None
    conn = WhatsAppBusinessConnection.objects.filter(company=customer.company,
                                                       notifications_enabled=True, messaging_mode="automatic").first()
    if not conn or not conn.event_settings.get(event, False):
        return None
    preference = CustomerNotificationPreference.objects.filter(
        company=customer.company, customer=customer, whatsapp_opt_in=True,
    ).first()
    template = WhatsAppMessageTemplate.objects.filter(
        company=customer.company, event=event, approval_status="APPROVED",
    ).first()
    number = _phone(customer)
    access = ensure_portal_access(customer)
    if not preference or not number or not access.is_active:
        return None
    send_ready = bool(conn.connected and template
                      and getattr(settings, "WHATSAPP_SEND_ENABLED", False)
                      and settings.CELERY_BROKER_URL)
    if not conn.connected:
        failure = "WhatsApp API disconnected. Use manual share."
    elif not template:
        failure = "Approved Meta template missing. Use manual share."
    else:
        failure = "WhatsApp sender or Celery broker is disabled."
    log, created = WhatsAppNotificationLog.objects.get_or_create(
        company=customer.company, idempotency_key=str(event_key)[:180],
        defaults={
            "branch":customer.branch, "customer": customer, "job":job,
            "event":event, "phone":number, "template_name":template.template_name if template else "",
            "status": "queued" if send_ready else "failed",
            "error_detail": "" if send_ready else failure,
        },
    )
    if created and send_ready:
        def enqueue():
            try:
                send_whatsapp_notification.delay(str(log.pk))
            except Exception:
                WhatsAppNotificationLog.objects.filter(pk=log.pk, status="queued").update(
                    status="failed", error_detail="WhatsApp delivery queue unavailable. Use manual share."
                )
        transaction.on_commit(enqueue)
    return log


@shared_task
def send_whatsapp_notification(log_id):
    # Do not retry uncertain provider responses: Meta may have accepted the message
    # before a timeout, and resending could duplicate a customer notification.
    if not getattr(settings, "WHATSAPP_SEND_ENABLED", False):
        return "disabled"
    with transaction.atomic():
        log = WhatsAppNotificationLog.objects.select_for_update().select_related("customer","job","company").get(pk=log_id)
        if log.status != "queued":
            return log.status
        conn = WhatsAppBusinessConnection.objects.filter(company=log.company, connected=True,
                                                           notifications_enabled=True, messaging_mode="automatic").first()
        opt_in = CustomerNotificationPreference.objects.filter(
            company=log.company, customer=log.customer, whatsapp_opt_in=True,
        ).exists()
        template = WhatsAppMessageTemplate.objects.filter(
            company=log.company, event=log.event, approval_status="APPROVED",
        ).first()
        access = ensure_portal_access(log.customer) if log.customer else None
        if not conn or not opt_in or not template or not access or not access.is_active:
            log.status = "skipped"
            log.error_detail = "Disconnected, opted out, missing template or revoked link."
            log.save(update_fields=["status", "error_detail", "updated_at"])
            return log.status
        log.status = "sending"
        log.attempt_count += 1
        log.save(update_fields=["status", "attempt_count", "updated_at"])

    # Network calls are outside the DB lock. 'sending' avoids parallel
    # duplicate attempts; the Meta API does not promise exactly-once sends.
    try:
        response = send_template_message(
            conn.phone_number_id, decrypt_token(conn.encrypted_access_token),
            log.phone, template.template_name, template.language,
            log.job.vehicle.registration if log.job else "Your vehicle",
            log.job.job_number if log.job else "Tracking",
            issue_link(access),
        )
        meta_id = (response.get("messages") or [{}])[0].get("id")
        if not meta_id:
            raise ValueError("Meta did not return a WhatsApp message ID.")
        WhatsAppNotificationLog.objects.filter(pk=log_id, status="sending").update(
            status="sent", meta_message_id=str(meta_id), error_detail="",
        )
        return "sent"
    except Exception:
        WhatsAppNotificationLog.objects.filter(pk=log_id, status="sending").update(
            status="failed",
            error_detail="Meta acceptance could not be confirmed. Do not blindly retry; use manual share if needed.",
        )
        return "failed"
