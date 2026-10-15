"""Opt-in Celery billing jobs. Financial mutation happens only in the server ledger."""
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone


@shared_task(name="cubegear.credits.renew_features", ignore_result=True)
def renew_due_credit_features():
    if not getattr(settings, "CREDIT_AUTOMATIC_BILLING_ENABLED", False):
        return {"skipped": "automatic billing disabled"}

    from django.core.management import call_command
    call_command("charge_credit_features")
    return {"status": "feature renewal completed"}


@shared_task(name="cubegear.credits.bill_storage", ignore_result=True)
def bill_due_storage_credits():
    if not getattr(settings, "CREDIT_AUTOMATIC_BILLING_ENABLED", False):
        return {"skipped": "automatic billing disabled"}

    from apps.credits.models import StorageCreditPolicy
    from django.core.management import call_command
    policy = StorageCreditPolicy.objects.filter(is_active=True).order_by("pk").first()
    if not policy:
        return {"skipped": "storage billing disabled"}

    # Monthly snapshot billing is taken only on the last calendar day.
    # It is NOT GB-second integration; usage-accurate metering is a separate
    # feature requiring a persisted interval ledger.
    today = timezone.localdate()
    if policy.billing_period == "monthly" and getattr(settings, "CREDIT_POSTPAID_BILLING_ENABLED", False):
        return {"skipped": "monthly storage charges belong to the postpaid statement"}
    if policy.billing_period == "monthly" and (today + timedelta(days=1)).month == today.month:
        return {"skipped": "monthly billing waits for the final calendar day"}

    call_command("bill_storage_credits")
    return {"status": "storage billing completed"}


@shared_task(name="cubegear.credits.meter_postpaid", ignore_result=True)
def record_postpaid_usage():
    from .postpaid_billing import meter_all_active_features, sample_all_storage
    if not (getattr(settings, "CREDIT_POSTPAID_BILLING_ENABLED", False)
            and getattr(settings, "CREDIT_AUTOMATIC_BILLING_ENABLED", False)):
        return {"skipped": "postpaid cycle disabled"}
    return {
        **meter_all_active_features(),
        **sample_all_storage(),
    }


@shared_task(name="cubegear.credits.settle_postpaid_monthly", ignore_result=True)
def settle_monthly_postpaid_credits():
    from .postpaid_billing import settle_due_months
    return settle_due_months()
