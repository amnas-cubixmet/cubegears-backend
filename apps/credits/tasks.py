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
    if settings.CREDIT_POSTPAID_FEATURE_BILLING_ENABLED:
        logger.info("Prepaid feature renewals skipped in postpaid mode")
        return "postpaid"
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


@shared_task(bind=True, ignore_result=True, name="credits.meter_postpaid_storage")
def meter_postpaid_storage(self):
    """Measure current bytes once per configured interval. No wallet debits."""
    if not settings.CREDIT_BILLING_CELERY_ENABLED:
        return "disabled"
    from apps.companies.models import Company
    from .models import StorageCreditPolicy
    from .postpaid import record_storage_interval
    policy = StorageCreditPolicy.objects.filter(
        is_active=True, billing_mode="metered_monthly"
    ).first()
    if not policy:
        return "disabled"
    results = {"metered": 0, "not_due": 0, "error": 0}
    for company in Company.objects.filter(is_active=True).iterator():
        try:
            status, _ = record_storage_interval(company, policy=policy)
            results[status] = results.get(status, 0) + 1
        except Exception:
            results["error"] += 1
            logger.exception("Storage meter failed for company=%s", company.pk)
    return results


@shared_task(bind=True, ignore_result=True, name="credits.settle_previous_month")
def settle_previous_month(self):
    """Month closes on the first day, 00:20 in configured local timezone."""
    if not settings.CREDIT_BILLING_CELERY_ENABLED:
        return "disabled"
    from .postpaid import run_monthly_settlement
    return run_monthly_settlement()
