"""Celery email entry point, sharing the same transport as optional direct SMTP."""
from celery import shared_task
from django.conf import settings
from .email_transport import send_account_email


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
    return send_account_email(subject, message, recipient_list, from_email,
                              action_url, action_label)
