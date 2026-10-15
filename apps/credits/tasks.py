"""Opt-in Celery entry points for CubeGear's EXISTING idempotent credit billing.

Do not run both this beat schedule and external cron/task-scheduler for the
same billing commands. These jobs do not invent usage readings or payments.
"""
import logging
from celery import shared_task
from django.conf import settings
from django.core.management import call_command

logger = logging.getLogger(__name__)


@shared_task(bind=True, ignore_result=True, name="credits.renew_due_features")
def renew_due_features(self):
    if not settings.CREDIT_BILLING_CELERY_ENABLED:
        logger.info("Feature credit billing disabled: no charge")
        return "disabled"
    # The Django management command calls the existing atomic/idempotent
    # billing service, which uses stable renewal references.
    call_command("charge_credit_features")
    return "completed"


@shared_task(bind=True, ignore_result=True, name="credits.bill_due_storage")
def bill_due_storage(self):
    if not settings.CREDIT_BILLING_CELERY_ENABLED:
        logger.info("Storage credit billing disabled: no charge")
        return "disabled"
    # This is currently period-snapshot billing, NOT GB-hours/postpaid.
    call_command("bill_storage_credits")
    return "completed"
