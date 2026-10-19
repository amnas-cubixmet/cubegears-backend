"""Conservative time-weighted monthly SaaS storage metering.

This is OPT-IN and never runs alongside the older snapshot billing command.
Rates are credits per stored GB per *calendar month*. Celery samples actual
MediaFile rows every configured number of seconds. Unobserved worker downtime
is not silently billed; late intervals are capped and logged.

Each closed period has one exact company/month reference in CreditLedger.
No browser-provided usage or credit amount is trusted.
"""
import logging
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.companies.models import Company
from .models import (
    CreditLedger, StorageCreditPolicy, StorageMeterCursor,
    StorageMeterPeriod, StorageMeterReading,
)
from .service import change_balance, wallet_for
from .storage_billing import storage_usage


LOG = logging.getLogger(__name__)
GB = Decimal(1024 ** 3)
ZERO = Decimal("0")
PRECISION = Decimal("0.00000001")
CENTS = Decimal("0.01")


def _month_bounds(period):
    year, month = map(int, period.split("-"))
    if not 1 <= month <= 12:
        raise ValueError("Invalid billing month.")
    tz = timezone.get_default_timezone()
    start = timezone.make_aware(datetime(year, month, 1), tz)
    end = timezone.make_aware(
        datetime(year + (month == 12), 1 if month == 12 else month + 1, 1), tz,
    )
    return start, end


def _period_for(point):
    return timezone.localtime(point, timezone.get_default_timezone()).strftime("%Y-%m")


def _policy():
    if not settings.STORAGE_MONTHLY_METERED_ENABLED:
        return None
    # Older StorageUsageInterval metering is selected using billing_mode.
    # Never sample the same merchant storage using BOTH independent engines.
    if StorageCreditPolicy.objects.filter(
        is_active=True, billing_mode="metered_monthly",
    ).exists():
        raise ValueError(
            "Legacy monthly storage engine is active. Disable it before enabling "
            "the alternative time-weighted storage meter."
        )
    policy = StorageCreditPolicy.objects.filter(
        is_active=True, billing_period="monthly", billing_mode="snapshot",
    ).order_by("pk").first()
    if policy and (policy.included_gb < 0 or policy.credits_per_gb < 0):
        raise ValueError("Negative metered storage rate / allowance is forbidden.")
    return policy


@transaction.atomic
def meter_company_storage(company, *, at=None, mutation=False):
    """Persist a real server-side measurement and accrue observed time once."""
    policy = _policy()
    if policy is None:
        return "disabled", None
    at = at or timezone.now()
    if timezone.is_naive(at):
        raise ValueError("Meter timestamp must include timezone.")
    if at > timezone.now() + timedelta(minutes=1):
        raise ValueError("Future storage metering is not allowed.")

    cursor, _ = StorageMeterCursor.objects.get_or_create(company=company)
    cursor = StorageMeterCursor.objects.select_for_update().get(pk=cursor.pk)
    if cursor.sampled_at and at < cursor.sampled_at:
        return "already_metered", None
    if cursor.sampled_at and at == cursor.sampled_at and not mutation:
        return "already_metered", None

    from .monthly_policy import policy as collection_policy
    from .runtime_billing import policy as runtime_policy
    runtime = runtime_policy()
    interval = runtime.storage_tracking_interval_seconds if runtime else (policy.meter_interval_seconds if collection_policy() else settings.STORAGE_METER_INTERVAL_SECONDS)
    if (runtime or collection_policy()) and cursor.sampled_at and not mutation and (at - cursor.sampled_at).total_seconds() < interval:
        return 'not_due', None
    # No MediaFile bytes can be invented or submitted by a browser.
    bytes_used = storage_usage(company)
    billable = max(ZERO, Decimal(bytes_used) / GB - policy.included_gb).quantize(
        Decimal("0.000001"), rounding=ROUND_HALF_UP,
    )
    credited = ZERO
    covered = 0
    if cursor.sampled_at:
        elapsed = int((at - cursor.sampled_at).total_seconds())
        from .monthly_policy import policy as collection_policy
        max_gap = int(interval) * 2
        # Never back-bill intervals during which the worker was down and file
        # changes could have gone unobserved.
        # A transactional change counter proves unchanged bytes even while Beat
        # is down. Snapshot mode retains its conservative no-backfill behavior.
        tracked = bool(runtime and runtime.event_tracking_enabled)
        start = cursor.sampled_at if tracked or elapsed <= max_gap else at
        if elapsed > max_gap and not tracked:
            LOG.warning(
                "Storage metering gap for company %s: %s seconds unobserved; no charge for the gap",
                company.pk, elapsed,
            )
        while start < at:
            month = _period_for(start)
            _, end = _month_bounds(month)
            segment_end = min(end, at)
            seconds = int((segment_end - start).total_seconds())
            if seconds <= 0:
                break
            month_start, month_end = _month_bounds(month)
            month_seconds = cursor.rate_duration_seconds or int((month_end - month_start).total_seconds())
            cost = (
                cursor.billable_gb * cursor.rate_per_gb
                * Decimal(seconds) / Decimal(month_seconds)
            ).quantize(PRECISION, rounding=ROUND_HALF_UP)
            accrual, _ = StorageMeterPeriod.objects.select_for_update().get_or_create(
                company=company, period=month,
            )
            from .models import BillingCycleStatement
            if accrual.settled_at or BillingCycleStatement.objects.filter(
                company=company, month=month,
            ).exists():
                raise ValueError("Cannot meter storage into a finalized month.")
            accrual.accrued_credits += cost
            accrual.covered_seconds += seconds
            accrual.save(update_fields=["accrued_credits", "covered_seconds", "updated_at"])
            credited += cost
            covered += seconds
            start = segment_end

    reading = StorageMeterReading.objects.create(
        company=company, sampled_at=at, used_bytes=bytes_used,
        billable_gb=billable, rate_per_gb=policy.credits_per_gb,
        covered_seconds=covered, accrued_credits=credited,
    )
    cursor.rate_duration_seconds = runtime.rate_duration_seconds if runtime else 0
    cursor.sampled_at = at
    cursor.used_bytes = bytes_used
    cursor.billable_gb = billable
    cursor.rate_per_gb = policy.credits_per_gb
    cursor.save(update_fields=[
        "sampled_at", "used_bytes", "billable_gb", "rate_per_gb", "rate_duration_seconds", "updated_at",
    ])
    return "metered", reading


def meter_all_company_storage(*, at=None):
    if _policy() is None:
        return {"disabled": 1}
    result = {"metered": 0, "failed": 0}
    for company in Company.objects.filter(is_active=True).iterator():
        try:
            status, _ = meter_company_storage(company, at=at)
            if status == "metered":
                result["metered"] += 1
        except Exception:
            result["failed"] += 1
            LOG.exception("Storage meter failed for company %s", company.pk)
    return result


@transaction.atomic
def settle_company_storage(company, period, *, at=None, dry_run=False):
    """Bill only a CLOSED calendar month; retries never duplicate wallet debits."""
    if not settings.STORAGE_MONTHLY_METERED_ENABLED:
        return "disabled", ZERO
    from .calendar_billing import active_policy
    if active_policy():
        return "calendar_policy", ZERO
    at = at or timezone.now()
    start, end = _month_bounds(period)
    if at < end:
        return "period_open", ZERO

    from .monthly_policy import collection_waiting
    if collection_waiting(period, at, dry_run=dry_run):
        return "not_due", ZERO

    live_policy = _policy()
    cursor = StorageMeterCursor.objects.select_for_update().filter(company=company).first()
    # Must observe the month-end boundary before finalizing its amount.
    if live_policy is not None and (cursor is None or cursor.sampled_at is None or cursor.sampled_at < end):
        return "awaiting_meter", ZERO
    row = StorageMeterPeriod.objects.select_for_update().filter(
        company=company, period=period,
    ).first()
    if row is None:
        return "no_usage", ZERO
    credits = row.accrued_credits.quantize(CENTS, rounding=ROUND_HALF_UP)
    if row.settled_at:
        return "already_billed", credits

    legacy_ref = f"storage:{company.pk}:monthly:{period}"
    if CreditLedger.objects.filter(company=company, reference=legacy_ref).exists():
        if not dry_run:
            row.settled_at = at
            row.save(update_fields=["settled_at", "updated_at"])
        return "legacy_billed", credits
    ref = f"storage-metered:{company.pk}:{period}"
    if dry_run:
        return "would_charge", credits
    wallet_for(company)
    try:
        _, created = change_balance(
            company, -credits, kind="storage_charge", reference=ref,
            description=(
                f"Metered storage {period}: {row.covered_seconds} observed seconds, "
                f"{credits} credit charge"
            ),
        )
    except ValueError:
        from .billing_notices import record_billing_notice
        record_billing_notice(company, "storage", period, "insufficient", credits)
        return "insufficient_credits", credits
    row.settled_at = at
    row.save(update_fields=["settled_at", "updated_at"])
    if created:
        from .billing_notices import record_billing_notice
        record_billing_notice(company, "storage", period, "paid", credits)
    return ("charged" if created else "already_billed"), credits


def settle_due_storage(*, at=None, dry_run=False):
    at = at or timezone.now()
    month = _period_for(at)
    pending = StorageMeterPeriod.objects.filter(
        period__lt=month, settled_at__isnull=True,
    ).select_related("company").order_by("period", "company_id")
    report = {}
    for row in pending.iterator():
        try:
            status, _ = settle_company_storage(
                row.company, row.period, at=at, dry_run=dry_run,
            )
        except Exception:
            status = "failed"
            LOG.exception("Storage settlement failed: company=%s month=%s", row.company_id, row.period)
        report[status] = report.get(status, 0) + 1
    return report
