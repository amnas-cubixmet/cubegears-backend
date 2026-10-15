"""Guarded scheduling entry points for company-credit billing.

Actual ledger writes still go through the existing atomic, idempotent Django
services. Do not activate billing automatically until deployment has been
reviewed and an explicit flag has been configured.
"""
import logging

from celery import shared_task
from django.conf import settings

logger = logging.getLogger(__name__)


@shared_task(name="cubegear.credits.renew_features", ignore_result=True)
def renew_due_credit_features():
    if not getattr(settings, "CREDIT_AUTOMATIC_BILLING_ENABLED", False):
        return {"skipped": "automatic billing disabled"}
    from apps.credits.management.commands.charge_credit_features import Command

    # Existing command uses next_charge_at, row-level locks, wallet ledger and
    # insufficient-balance suspension. It does not perform fake credit writes.
    from django.core.management import call_command
    call_command("charge_credit_features")
    return {"status": "feature renewal completed"}


@shared_task(name="cubegear.credits.bill_storage", ignore_result=True)
def bill_due_storage_credits():
    if not getattr(settings, "CREDIT_AUTOMATIC_BILLING_ENABLED", False):
        return {"skipped": "automatic billing disabled"}
    from django.core.management import call_command
    call_command("bill_storage_credits")
    return {"status": "storage billing completed"}
