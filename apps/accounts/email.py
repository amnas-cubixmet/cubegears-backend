"""Synchronous delivery now; retain the worker implementation for later use."""
from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction


def queue_email(subject, message, recipient_list):
    recipients = list(recipient_list)

    def deliver():
        return send_mail(subject, message, settings.DEFAULT_FROM_EMAIL,
                         recipients, fail_silently=False)

        # Future Celery delivery: replace the return above with these lines.
        # from .tasks import send_email_task
        # return send_email_task.delay(subject=subject, message=message,
        #     recipient_list=recipients, from_email=settings.DEFAULT_FROM_EMAIL)

    # Do not email credentials for a transaction that later rolls back.
    # Errors are logged by Django's on_commit handler; do not leak account
    # existence through the public reset/magic-link endpoints.
    transaction.on_commit(deliver, robust=True)
