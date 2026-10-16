"""Celery worker is the only application path permitted to call Django email."""
from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMultiAlternatives, send_mail
from django.template.loader import render_to_string


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    retry_kwargs={"max_retries": 5},
    ignore_result=True,
)
def send_email_task(self, subject, message, recipient_list, from_email=None,
                    action_url=None, action_label=None):
    if not getattr(settings, "EMAIL_CELERY_ENABLED", False):
        return {"disabled": True}
    from_address = from_email or settings.DEFAULT_FROM_EMAIL
    if action_url:
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
            from_email=from_address, to=recipient_list,
        )
        email.attach_alternative(html, "text/html")
        accepted = email.send(fail_silently=False)
    else:
        accepted = send_mail(
            subject=subject,
            message=message,
            from_email=from_address,
            recipient_list=recipient_list,
            fail_silently=False,
        )
    if accepted != 1:
        raise RuntimeError("Email provider did not accept the message.")
    return {"acceptedByProvider": True}
