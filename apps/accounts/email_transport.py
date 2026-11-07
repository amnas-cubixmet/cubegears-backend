"""Shared SMTP transport for synchronous hosting and Celery worker delivery.

Never log password/magic links, recipients, or provider credentials.
"""
from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection, send_mail
from django.template.loader import render_to_string


def send_account_email(subject, message, recipient_list, from_email=None,
                       action_url=None, action_label=None, *, smtp_timeout_seconds=None):
    """Send through Django's configured email backend, raising on failure."""
    from_address = from_email or settings.DEFAULT_FROM_EMAIL
    # Only signup's direct SMTP delivery overrides the backend default (15s).
    # Celery and all other account emails retain their configured transport.
    connection = (get_connection(timeout=smtp_timeout_seconds)
                  if smtp_timeout_seconds is not None else None)
    if action_url:
        context = {
            "subject": subject,
            "body": message,
            "action_url": action_url,
            "action_label": action_label or "Open CubixGear",
            "brand": "CubeGears",
        }
        plain = render_to_string("accounts/email/action.txt", context)
        html = render_to_string("accounts/email/action.html", context)
        email = EmailMultiAlternatives(
            subject=subject, body=plain, from_email=from_address,
            to=recipient_list, connection=connection,
        )
        email.attach_alternative(html, "text/html")
        accepted = email.send(fail_silently=False)
    else:
        accepted = send_mail(
            subject=subject, message=message, from_email=from_address,
            recipient_list=recipient_list, fail_silently=False,
            connection=connection,
        )
    if accepted != 1:
        raise RuntimeError("Email provider did not accept the message.")
    return {"acceptedByProvider": True}
