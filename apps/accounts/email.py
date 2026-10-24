"""Send account emails after commit via explicit Celery or synchronous SMTP.

No email is sent if the configured delivery mode is disabled or incomplete.
Never fall back from a failed Celery dispatch to SMTP (avoids duplicates).
"""
import logging

from django.conf import settings
from django.db import transaction
from .email_transport import send_account_email

LOG = logging.getLogger(__name__)


def email_delivery_mode():
    """Return 'disabled', 'smtp' or 'pending' (Celery broker dispatch)."""
    mode = getattr(settings, "ACCOUNT_EMAIL_DELIVERY_MODE", "disabled")
    if mode == "smtp":
        # Production mode must never accidentally print signed links to console.
        smtp = settings.EMAIL_BACKEND == "django.core.mail.backends.smtp.EmailBackend"
        credentials = all((
            settings.EMAIL_HOST, settings.EMAIL_HOST_USER,
            settings.EMAIL_HOST_PASSWORD, settings.DEFAULT_FROM_EMAIL,
        ))
        return "smtp" if smtp and credentials and not (
            settings.EMAIL_USE_SSL and settings.EMAIL_USE_TLS
        ) else "disabled"
    if mode == "celery" and settings.EMAIL_CELERY_ENABLED:
        broker = settings.CELERY_BROKER_URL
        if broker.startswith(("redis://", "rediss://")):
            return "pending"
    return "disabled"


def queue_email(subject, message, recipient_list, *, action_url=None,
                action_label=None, on_delivery=None):
    """Dispatch only after commit. 'queued' != delivered; 'sent' == SMTP accepted.

    Direct SMTP is optional and synchronous for hosts without Celery support.
    For durable at-least-once delivery use a transactional outbox and a worker;
    web process failures between DB commit and SMTP cannot be recovered here.
    """
    recipients = list(recipient_list)
    state = {"status": email_delivery_mode()}

    def notify(status):
        state["status"] = status
        if on_delivery:
            on_delivery(status)

    def deliver():
        mode = email_delivery_mode()
        if mode == "disabled":
            notify("disabled")
            return
        try:
            if mode == "smtp":
                send_account_email(
                    subject, message, recipients, settings.DEFAULT_FROM_EMAIL,
                    action_url, action_label,
                )
                notify("sent")
                return
            from config.celery import app as celery_app
            from .tasks import send_email_task
            celery_app.send_task(
                send_email_task.name,
                args=[subject, message, recipients, settings.DEFAULT_FROM_EMAIL,
                      action_url, action_label],
            )
        except Exception as exc:
            notify("failed")
            # Do not log signed action links, recipients, or provider exceptions.
            LOG.error("Account email dispatch failed (%s)", type(exc).__name__)
        else:
            notify("queued")

    transaction.on_commit(deliver, robust=True)
    return state
