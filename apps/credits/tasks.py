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
    if (settings.CREDIT_POSTPAID_FEATURE_BILLING_ENABLED
            or getattr(settings, "CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED", False)):
        logger.info("Prepaid feature renewals skipped in postpaid mode")
        return "postpaid"
    call_command("charge_credit_features")
    return "completed"


@shared_task(bind=True, ignore_result=True, name="credits.bill_due_storage")
def bill_due_storage(self):
    if not settings.CREDIT_BILLING_CELERY_ENABLED:
        logger.info("Storage credit billing disabled: no charge")
        return "disabled"
    if getattr(settings, "STORAGE_MONTHLY_METERED_ENABLED", False):
        logger.info("Legacy storage snapshot billing skipped for newer monthly meter")
        return "monthly_metered"
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


@shared_task(bind=True, ignore_result=True, name="credits.sync_postpaid_feature_rates")
def sync_postpaid_feature_rates(self):
    """Reprice future intervals when active seats or staff counts change."""
    if not settings.CREDIT_BILLING_CELERY_ENABLED or not settings.CREDIT_POSTPAID_FEATURE_BILLING_ENABLED:
        return "disabled"
    from apps.companies.models import Company
    from .postpaid import refresh_postpaid_feature_rates
    changed = 0
    failures = 0
    for company in Company.objects.filter(is_active=True).iterator():
        try:
            changed += refresh_postpaid_feature_rates(company)
        except Exception:
            failures += 1
            logger.exception("Postpaid seat rate reconciliation failed for company=%s", company.pk)
    return {"changed": changed, "errors": failures}


# New monthly time-metered tasks use separate immutable reading/period tables.
# These names only enter Beat when enabled explicitly in config/settings.py.
@shared_task(ignore_result=True, name="cubegear.storage.monthly.sample")
def sample_monthly_storage():
    if not (settings.CREDIT_BILLING_CELERY_ENABLED
            and settings.STORAGE_MONTHLY_METERED_ENABLED):
        return {"disabled": 1}
    from .monthly_storage import meter_all_company_storage
    return meter_all_company_storage()


@shared_task(ignore_result=True, name="cubegear.storage.monthly.settle")
def settle_monthly_storage():
    if not (settings.CREDIT_BILLING_CELERY_ENABLED
            and settings.STORAGE_MONTHLY_METERED_ENABLED):
        return {"disabled": 1}
    from .monthly_storage import settle_due_storage
    return settle_due_storage()


@shared_task(ignore_result=True, name="cubegear.features.monthly.sample")
def sample_monthly_features():
    if not (settings.CREDIT_BILLING_CELERY_ENABLED
            and settings.CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED):
        return {"disabled": 1}
    from .monthly_features import meter_all_postpaid_features
    return meter_all_postpaid_features()


@shared_task(ignore_result=True, name="cubegear.features.monthly.settle")
def settle_monthly_features():
    if not (settings.CREDIT_BILLING_CELERY_ENABLED
            and settings.CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED):
        return {"disabled": 1}
    from .monthly_features import settle_due_postpaid_features
    return settle_due_postpaid_features()


@shared_task(ignore_result=True, name="cubegear.billing.send_monthly_notices")
def send_monthly_billing_notices():
    """Dispatch durable admin email outbox; never mutates the credit ledger."""
    if not (getattr(settings, "EMAIL_CELERY_ENABLED", False)
            and getattr(settings, "BILLING_EMAIL_ALERTS_ENABLED", False)):
        return {"disabled": 1}
    from .billing_notices import dispatch_pending_billing_notices
    return dispatch_pending_billing_notices()


@shared_task(ignore_result=True, name="credits.collect_due_monthly_bills")
def collect_due_monthly_bills():
    if not settings.CREDIT_BILLING_CELERY_ENABLED:
        return 'disabled'
    from .monthly_policy import policy, retry_after_topup
    if not policy():
        return 'disabled'
    from apps.companies.models import Company
    for company in Company.objects.filter(is_active=True).iterator():
        retry_after_topup(company)
    return 'completed'
