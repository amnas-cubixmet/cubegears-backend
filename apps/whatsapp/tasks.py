"""Idempotent, consent-gated WhatsApp delivery using existing Celery worker."""
import re
from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from apps.jobs.models import JobEstimate
from apps.jobs.workflow import workflow_state
from apps.customers.estimate_review import public_estimate
from apps.customers.portal_tokens import ensure_portal_access, issue_link
from .meta import approved_templates, decrypt_token, send_template_message
from .phone import normalize_recipient
from .models import (
    WhatsAppBusinessConnection, WhatsAppMessageTemplate,
    CustomerNotificationPreference, WhatsAppNotificationLog,
)

EVENTS = (
    "tracking_link", "staff_onboarding", "job_created", "inspection_completed",
    "work_started", "status_changed", "work_completed",
    "ready_delivery", "invoice_published", "estimate_shared",
    "estimate_reminder", "test",
)


def _phone(customer):
    return normalize_recipient(customer.whatsapp or customer.phone)


_ESTIMATE_EVENT_KEY = re.compile(
    r"^estimate:([0-9a-fA-F-]{36}):(shared|reminder:[1-9][0-9]*)$"
)


def _active_estimate(event, event_key, customer, job):
    """Check the saved quote at queue time AND immediately before provider send.

    Do not send messages about withdrawn, rejected, superseded or completed
    estimates. The event key refers only to a UUID, never the private link.
    """
    if event not in {"estimate_shared", "estimate_reminder"}:
        return None
    match = _ESTIMATE_EVENT_KEY.fullmatch(str(event_key))
    if not match or not job or not customer:
        return None
    if (match.group(2) == "shared") != (event == "estimate_shared"):
        return None
    if (job.company_id != customer.company_id or job.customer_id != customer.pk
            or job.vehicle.customer_id != customer.pk):
        return None
    quote = JobEstimate.objects.filter(
        pk=match.group(1), job=job, company=job.company,
        status="Awaiting Customer",
    ).first()
    if not quote or workflow_state(job)["current"] != "estimate":
        return None
    latest_id = job.estimates.order_by("-version", "-created_at").values_list(
        "pk", flat=True
    ).first()
    if latest_id != quote.pk or not public_estimate(quote)["reviewable"]:
        return None
    return quote


def queue_customer_notification(customer, event, event_key, job=None):
    """Create at most one per-event record; never send without explicit opt-in."""
    if event not in EVENTS or not customer or not customer.company_id:
        return None
    if event in {"estimate_shared", "estimate_reminder"} and not _active_estimate(
        event, event_key, customer, job
    ):
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
        estimate_valid = (
            log.event not in {"estimate_shared", "estimate_reminder"}
            or _active_estimate(log.event, log.idempotency_key, log.customer, log.job)
        )
        if (not conn or not conn.event_settings.get(log.event, False)
                or not opt_in or not template or not access or not access.is_active
                or not estimate_valid):
            log.status = "skipped"
            log.error_detail = "Disconnected, opted out, missing template, revoked link or stale estimate."
            log.save(update_fields=["status", "error_detail", "updated_at"])
            return log.status
        log.status = "sending"
        log.attempt_count += 1
        log.save(update_fields=["status", "attempt_count", "updated_at"])

    # Network calls are outside the DB lock. 'sending' avoids parallel
    # duplicate attempts; the Meta API does not promise exactly-once sends.
    try:
        token = decrypt_token(conn.encrypted_access_token)
        still_approved = any(
            row.get("name") == template.template_name and row.get("language") == template.language
            for row in approved_templates(conn.waba_id, token)
        )
        if not still_approved:
            raise ValueError("Template approval is no longer valid.")
        response = send_template_message(
            conn.phone_number_id, token,
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


@shared_task(name="whatsapp.remind_open_estimates")
def remind_open_estimates():
    """Optional once-daily sweep for unaccepted, staff-shared estimates.

    A workshop opts in separately to the reminder event and selects 24 or 48
    hours in event_settings. One key per quote and elapsed cadence bucket
    ensures repeated Beat executions never duplicate that notification.
    Delayed jobs that no longer need review are skipped by the worker.
    """
    if not (
        getattr(settings, "WHATSAPP_ESTIMATE_REMINDERS_ENABLED", False)
        and getattr(settings, "WHATSAPP_SEND_ENABLED", False)
        and settings.CELERY_BROKER_URL
    ):
        return 0
    sent_to_queue = 0
    now = timezone.now()
    connections = WhatsAppBusinessConnection.objects.filter(
        connected=True, messaging_mode="automatic", notifications_enabled=True
    ).select_related("company")
    for conn in connections.iterator():
        if not conn.event_settings.get("estimate_reminder", False):
            continue
        cadence = conn.event_settings.get("estimate_reminder_hours", 24)
        if type(cadence) is not int or cadence not in {24, 48}:
            continue
        # Bounded per-tenant scan. Latest quotes are checked again after select.
        quotes = JobEstimate.objects.filter(
            company=conn.company, status="Awaiting Customer",
            job__company=conn.company, job__customer__status="active",
        ).select_related("job__customer", "job__vehicle").order_by("updated_at")[:500]
        for quote in quotes:
            age_hours = (now - quote.updated_at).total_seconds() / 3600
            if age_hours < cadence:
                continue
            slot = int(age_hours // cadence)
            event_key = f"estimate:{quote.pk}:reminder:{slot}"
            if not _active_estimate(
                "estimate_reminder", event_key, quote.job.customer, quote.job
            ):
                continue
            log = queue_customer_notification(
                quote.job.customer, "estimate_reminder", event_key, job=quote.job
            )
            if log and log.status == "queued":
                sent_to_queue += 1
    return sent_to_queue
