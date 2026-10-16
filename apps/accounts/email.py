"""Queue all account emails to the configured Celery broker after commit.

No direct SMTP, console, locmem or other Django email backend is called by
web requests. Without Celery or Redis, no email is sent. Queued is NOT sent.
"""
import logging

from django.conf import settings
from django.db import transaction

LOG = logging.getLogger(__name__)


def email_delivery_mode():
    """Describe dispatch readiness without claiming SMTP or worker delivery."""
    if not getattr(settings, "EMAIL_CELERY_ENABLED", False):
        return "disabled"
    broker = getattr(settings, "CELERY_BROKER_URL", "")
    if not broker or not broker.startswith(("redis://", "rediss://")):
        return "disabled"
    return "pending"


def queue_email(subject, message, recipient_list, *, action_url=None,
                action_label=None, on_delivery=None):
    """Enqueue after a committed transaction; never send directly.

    The mutable status is 'pending' until the commit callback runs. On success
    it is 'queued' (broker-accepted, not necessarily delivered); failures are
    'failed' and are never silently retried using Django's SMTP path.

    on_delivery, where used, reports dispatch status only, not real delivery.
    Signed action URLs are never logged but are carried by the Redis broker;
    secure Redis accordingly. A durable outbox is needed to guarantee retries
    across broker outages after a database commit.
    """
    recipients = list(recipient_list)
    state = {"status": email_delivery_mode()}

    def deliver():
        if email_delivery_mode() == "disabled":
            state["status"] = "disabled"
            if on_delivery:
                on_delivery("disabled")
            return
        try:
            from config.celery import app as celery_app
            from .tasks import send_email_task
            celery_app.send_task(
                send_email_task.name,
                args=[
                    subject, message, recipients, settings.DEFAULT_FROM_EMAIL,
                    action_url, action_label,
                ],
            )
        except Exception as exc:
            state["status"] = "failed"
            LOG.error("Celery email dispatch failed: %s", type(exc).__name__)
            if on_delivery:
                on_delivery("failed")
        else:
            state["status"] = "queued"
            if on_delivery:
                on_delivery("queued")

    transaction.on_commit(deliver, robust=True)
    return state
