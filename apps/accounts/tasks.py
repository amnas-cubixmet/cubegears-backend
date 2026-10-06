from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    retry_kwargs={"max_retries": 5},
)
def send_email_task(self, subject, message, recipient_list, from_email=None):
    send_mail(
        subject=subject,
        message=message,
        from_email=from_email or settings.DEFAULT_FROM_EMAIL,
        recipient_list=recipient_list,
        fail_silently=False,
    )
    return {"sent": len(recipient_list), "recipients": recipient_list}
