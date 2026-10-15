"""Transactional account emails sent directly by Django (Celery remains optional).

A successful SMTP send means the provider accepted the message, not that the
recipient opened it. Failed attempts must not turn a committed staff creation
into a retryable create operation.
"""
from django.conf import settings
from django.core.mail import EmailMultiAlternatives, send_mail
from django.db import transaction
from django.template.loader import render_to_string


def email_delivery_mode():
    """Only diagnostic mode info, never disclose SMTP credentials or links."""
    backend = settings.EMAIL_BACKEND
    if backend.endswith(".console.EmailBackend"):
        return "console_only"
    if backend.endswith(".locmem.EmailBackend") or backend.endswith(".filebased.EmailBackend"):
        return "local_only"
    if backend.endswith(".dummy.EmailBackend"):
        return "disabled"
    return "queued"


def queue_email(subject, message, recipient_list, *, action_url=None,
                action_label=None, on_delivery=None):
    """Send on successful DB commit and report outcome to trusted callers.

    Returns a mutable status dict. on_delivery gets 'submitted' or 'failed'
    once a send has actually been attempted. Never log security URLs/tokens.
    An exception is allowed to reach Django's robust=True on_commit logging,
    without aborting the committed transaction.
    """
    recipients = list(recipient_list)
    state = {"status": email_delivery_mode()}

    def deliver():
        # Optional background delivery for plain notification messages.
        # Security emails with action URLs or delivery callbacks still use the
        # existing synchronous path to preserve immediate success reporting.
        if settings.EMAIL_CELERY_ENABLED and not action_url and on_delivery is None:
            try:
                from .tasks import send_email_task
                send_email_task.delay(subject, message, recipients, settings.DEFAULT_FROM_EMAIL)
                state["status"] = "queued"
                return
            except Exception:
                # Never drop a committed notification if Redis/Beat is down.
                # Attempt the original direct SMTP send instead.
                pass
        try:
            if not action_url:
                count = send_mail(subject, message, settings.DEFAULT_FROM_EMAIL,
                                  recipients, fail_silently=False)
            else:
                context = {
                    "subject": subject,
                    "body": message,
                    "action_url": action_url,
                    "action_label": action_label or "Open CubixGear",
                    "brand": "CubixGear",
                }
                plain = render_to_string("accounts/email/action.txt", context)
                html = render_to_string("accounts/email/action.html", context)
                email = EmailMultiAlternatives(
                    subject=subject, body=plain,
                    from_email=settings.DEFAULT_FROM_EMAIL, to=recipients,
                )
                email.attach_alternative(html, "text/html")
                count = email.send(fail_silently=False)

            status = email_delivery_mode()
            state["status"] = status if status != "queued" else ("submitted" if count else "failed")
            if on_delivery:
                on_delivery(state["status"])
        except Exception:
            state["status"] = "failed"
            if on_delivery:
                on_delivery("failed")
            raise

    transaction.on_commit(deliver, robust=True)
    return state
