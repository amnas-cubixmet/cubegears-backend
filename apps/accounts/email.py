"""Transaction-safe CubixGear account emails: readable plain text + branded HTML."""
from django.conf import settings
from django.core.mail import EmailMultiAlternatives, send_mail
from django.db import transaction
from django.template.loader import render_to_string


def queue_email(subject, message, recipient_list, *, action_url=None, action_label=None):
    """Send after commit so no login/setup links are sent for rolled-back accounts.

    When action_url is specified, send a branded HTML email with a plain-text
    fallback. Keep the legacy plain mail option for operational notifications.
    Do not log security links or their tokens.
    """
    recipients = list(recipient_list)

    def deliver():
        if not action_url:
            return send_mail(subject, message, settings.DEFAULT_FROM_EMAIL,
                             recipients, fail_silently=False)

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
        return email.send(fail_silently=False)

    transaction.on_commit(deliver, robust=True)
