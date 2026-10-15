"""Opt-in, ledger-backed monthly postpaid credits for feature time and storage.

Feature cycles use server-recorded activation, deactivation and periodic
meter timestamps. Storage month-weighted usage comes from trusted upload/delete
events (with a baseline on first instrumentation), never the browser.
This module never takes an INR payment or mints wallet credits.
"""
from calendar import monthrange
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction
from django.db.models import Sum, Count
from django.utils import timezone

from apps.companies.models import Company
from apps.saas.models import MediaFile, StorageEvent
from apps.saas.storage_ledger import ensure_baseline
from .models import (
    CompanyCreditFeature, CreditWallet, MonthlyCreditStatement,
    PostpaidFeatureState, PostpaidUsageRecord, StorageCreditPolicy,
    StorageMeterSample,
)
from .service import wallet_for, change_balance, billable_user_count, staff_count

CENT = Decimal("0.01")
PRECISION = Decimal("0.00000001")
GIB = Decimal(1024 ** 3)


def month_bounds(value):
    """Calendar month boundaries in the tenant platform's configured timezone."""
    local = timezone.localtime(value, ZoneInfo(settings.TIME_ZONE))
    start = datetime(local.year, local.month, 1, tzinfo=ZoneInfo(settings.TIME_ZONE))
    if local.month == 12:
        end = datetime(local.year + 1, 1, 1, tzinfo=ZoneInfo(settings.TIME_ZONE))
    else:
        end = datetime(local.year, local.month + 1, 1, tzinfo=ZoneInfo(settings.TIME_ZONE))
    return start, end


def _period(value):
    return timezone.localtime(value, ZoneInfo(settings.TIME_ZONE)).strftime("%Y-%m")


def _seconds(start, end):
    # UTC timestamp subtraction is robust around timezone offset changes.
    return Decimal(str(end.timestamp() - start.timestamp()))


def _month_credits(price, from_at, until_at):
    start, end = month_bounds(from_at)
    return (price * _seconds(from_at, until_at) / _seconds(start, end)).quantize(
        PRECISION, rounding=ROUND_HALF_UP,
    )


@transaction.atomic
def enable_postpaid_feature(company, feature, pricing, actor=None, now=None):
    """Enable a monthly feature without debiting wallet until month-end."""
    now = now or timezone.now()
    if not settings.CREDIT_POSTPAID_BILLING_ENABLED:
        raise ValueError("Monthly postpaid billing is disabled.")
    if pricing.billing_period != "monthly" or not pricing.is_available or not feature.is_active:
        raise ValueError("Only available monthly features support postpaid metering.")
    if MonthlyCreditStatement.objects.filter(
        company=company, status="pending", total_credits__gt=0,
    ).exists():
        raise ValueError("Please settle outstanding monthly storage or feature credits first.")
    wallet_for(company)
    old, _ = CompanyCreditFeature.objects.select_for_update().get_or_create(
        company=company, feature=feature,
    )
    if old.enabled and old.next_charge_at and old.next_charge_at > now:
        # Never start simultaneous prepaid and postpaid charging for one feature.
        return old, Decimal("0.00")

    meter, _ = PostpaidFeatureState.objects.select_for_update().get_or_create(
        company=company, feature=feature,
    )
    if meter.enabled:
        return old, Decimal("0.00")

    meter.enabled = True
    meter.meter_started_at = now
    meter.activated_at = now
    meter.base_credits = pricing.base_credits
    meter.per_user_credits = pricing.per_user_credits
    meter.per_staff_credits = pricing.per_staff_credits
    meter.save()

    old.enabled = True
    old.next_charge_at = None
    old.save(update_fields=["enabled", "next_charge_at", "updated_at"])

    if pricing.activation_credits > 0:
        PostpaidUsageRecord.objects.create(
            company=company, feature=feature, kind="feature_activation",
            period=_period(now), from_at=now, until_at=now,
            user_count=billable_user_count(company), staff_count=staff_count(company),
            credits=pricing.activation_credits,
            reference=f"postpaid-activation:{company.pk}:{feature.pk}:{uuid4().hex}",
        )
    return old, Decimal("0.00")


def _meter_one_locked(meter, until_at):
    if not meter.enabled or not meter.meter_started_at or meter.meter_started_at >= until_at:
        return 0
    count = 0
    users = billable_user_count(meter.company)
    staff = staff_count(meter.company)
    monthly = meter.base_credits + (meter.per_user_credits * users) + (meter.per_staff_credits * staff)
    if monthly < 0:
        raise ValueError("Negative monthly feature rates are forbidden.")
    cursor = meter.meter_started_at
    while cursor < until_at:
        _, month_end = month_bounds(cursor)
        end = min(until_at, month_end)
        if end <= cursor:
            break
        PostpaidUsageRecord.objects.create(
            company=meter.company, feature=meter.feature,
            kind="feature_usage", period=_period(cursor),
            from_at=cursor, until_at=end,
            user_count=users, staff_count=staff,
            credits=_month_credits(monthly, cursor, end),
            reference=f"postpaid-meter:{meter.pk}:{cursor.timestamp():.6f}:{end.timestamp():.6f}",
        )
        count += 1
        cursor = end
    meter.meter_started_at = until_at
    meter.save(update_fields=["meter_started_at", "updated_at"])
    return count


@transaction.atomic
def meter_feature_state(meter, now=None):
    locked = PostpaidFeatureState.objects.select_for_update().select_related(
        "company", "feature"
    ).get(pk=meter.pk)
    return _meter_one_locked(locked, now or timezone.now())


@transaction.atomic
def disable_postpaid_feature(company, feature, actor=None, now=None):
    now = now or timezone.now()
    state = PostpaidFeatureState.objects.select_for_update().filter(
        company=company, feature=feature, enabled=True,
    ).first()
    if not state:
        return False
    _meter_one_locked(state, now)
    state.enabled = False
    state.save(update_fields=["enabled", "updated_at"])
    CompanyCreditFeature.objects.filter(company=company, feature=feature).update(
        enabled=False, next_charge_at=None,
    )
    return True


def meter_all_active_features(now=None):
    """Worker cycle. Each state row lock ensures retries are non-duplicating."""
    if not (settings.CREDIT_POSTPAID_BILLING_ENABLED and settings.CREDIT_AUTOMATIC_BILLING_ENABLED):
        return {"skipped": "postpaid metering disabled"}
    now = now or timezone.now()
    recorded = 0
    for pk in PostpaidFeatureState.objects.filter(enabled=True).values_list("pk", flat=True).iterator():
        recorded += meter_feature_state(PostpaidFeatureState(pk=pk), now=now)
    return {"feature_segments": recorded}


def sample_all_storage(now=None):
    """Keep an auditable company snapshot each configured cycle."""
    if not (settings.CREDIT_POSTPAID_BILLING_ENABLED and settings.CREDIT_AUTOMATIC_BILLING_ENABLED):
        return {"skipped": "postpaid storage sampling disabled"}
    now = now or timezone.now()
    sampled = 0
    for company in Company.objects.filter(is_active=True).iterator():
        with transaction.atomic():
            ensure_baseline(company)
            totals = MediaFile.objects.filter(company=company).aggregate(
                bytes=Sum("size_bytes"), files=Count("id"),
            )
            # At most one sample per company per scheduled 60-second bucket.
            bucket = int(now.timestamp() // 60)
            _, created = StorageMeterSample.objects.get_or_create(
                reference=f"postpaid-storage:{company.pk}:{bucket}",
                defaults={
                    "company": company, "sampled_at": now,
                    "bytes_used": max(0, totals["bytes"] or 0),
                    "file_count": totals["files"] or 0,
                },
            )
            sampled += int(created)
    return {"storage_samples": sampled}


def storage_month_credits(company, month_start, month_end, policy):
    """Time-weighted billable GB from upload/delete events in the chosen month."""
    if not policy or not policy.is_active or policy.billing_period != "monthly":
        return Decimal("0.00"), {"gb_seconds": "0", "events": 0}
    if policy.included_gb < 0 or policy.credits_per_gb < 0:
        raise ValueError("Invalid storage pricing.")
    records = StorageEvent.objects.filter(
        company=company, occurred_at__lt=month_end,
    ).order_by("occurred_at", "created_at", "id")
    initial = records.filter(occurred_at__lt=month_start).last()
    used = max(0, initial.storage_after_bytes) if initial else 0
    cursor = month_start
    gb_seconds = Decimal("0")
    count = 0
    for event in records.filter(occurred_at__gte=month_start):
        seconds = _seconds(cursor, event.occurred_at)
        billable_gb = max(Decimal("0"), Decimal(used) / GIB - policy.included_gb)
        gb_seconds += billable_gb * max(Decimal("0"), seconds)
        used = max(0, event.storage_after_bytes)
        cursor = event.occurred_at
        count += 1
    remaining = max(Decimal("0"), _seconds(cursor, month_end))
    gb_seconds += max(Decimal("0"), Decimal(used) / GIB - policy.included_gb) * remaining
    month_seconds = _seconds(month_start, month_end)
    charge = (gb_seconds / month_seconds * policy.credits_per_gb).quantize(
        CENT, rounding=ROUND_HALF_UP,
    )
    return charge, {"gb_seconds": str(gb_seconds.quantize(Decimal("0.01"))), "events": count}


@transaction.atomic
def settle_company_month(company, period, now=None):
    """One immutable statement and at most one ledger debit per month.

    Insufficient prepaid credits leave a visible pending invoice. Retried tasks
    attempt the same invoice total and cannot debit a second time.
    """
    now = now or timezone.now()
    if not (settings.CREDIT_POSTPAID_BILLING_ENABLED and settings.CREDIT_AUTOMATIC_BILLING_ENABLED):
        return "disabled", None
    try:
        year, month = (int(v) for v in period.split("-"))
        start = datetime(year, month, 1, tzinfo=ZoneInfo(settings.TIME_ZONE))
    except (ValueError, TypeError):
        raise ValueError("Billing period must be YYYY-MM.")
    period_start, period_end = month_bounds(start)
    if period_end > now:
        raise ValueError("Cannot settle an unfinished calendar month.")

    # Serialize this company's charge and avoid changing an already-published invoice.
    wallet_for(company)
    wallet = CreditWallet.objects.select_for_update().get(company=company)
    existing = MonthlyCreditStatement.objects.select_for_update().filter(
        company=company, period=period,
    ).first()
    if existing is None:
        for pk in PostpaidFeatureState.objects.filter(
            company=company, enabled=True,
        ).values_list("pk", flat=True):
            with transaction.atomic():
                meter = PostpaidFeatureState.objects.select_for_update().select_related(
                    "company", "feature"
                ).get(pk=pk)
                _meter_one_locked(meter, period_end)

        raw_feature = PostpaidUsageRecord.objects.filter(
            company=company, period=period,
        ).aggregate(total=Sum("credits"))["total"] or Decimal("0")
        feature_total = raw_feature.quantize(CENT, rounding=ROUND_HALF_UP)
        policy = StorageCreditPolicy.objects.filter(
            is_active=True, billing_period="monthly",
        ).order_by("pk").first()
        storage_total, usage = storage_month_credits(company, period_start, period_end, policy)
        existing = MonthlyCreditStatement.objects.create(
            company=company, period=period,
            feature_credits=feature_total,
            storage_credits=storage_total,
            total_credits=feature_total + storage_total,
            details={
                "usageRecords": PostpaidUsageRecord.objects.filter(
                    company=company, period=period,
                ).count(),
                "storage": usage,
                "pricing": {
                    "storageRate": str(policy.credits_per_gb) if policy else "0",
                    "freeGb": str(policy.included_gb) if policy else "0",
                },
            },
        )
    if existing.status == "paid":
        return "already_paid", existing
    if existing.total_credits == 0:
        existing.status = "paid"
        existing.paid_at = now
        existing.save(update_fields=["status", "paid_at"])
        return "zero_usage", existing
    from .models import CreditLedger
    ref = f"postpaid-month:{company.pk}:{period}"
    if CreditLedger.objects.filter(company=company, reference=ref).exists():
        existing.status = "paid"
        existing.paid_at = now
        existing.save(update_fields=["status", "paid_at"])
        return "already_paid", existing
    try:
        change_balance(
            company, -existing.total_credits, kind="monthly_usage",
            reference=ref,
            description=f"Monthly SaaS feature and storage usage {period}",
        )
    except ValueError:
        return "insufficient_credits", existing
    existing.status = "paid"
    existing.paid_at = now
    existing.save(update_fields=["status", "paid_at"])
    return "charged", existing


def settle_due_months(now=None):
    """Close the last completed month and retry previously unpaid statements."""
    if not (settings.CREDIT_POSTPAID_BILLING_ENABLED and settings.CREDIT_AUTOMATIC_BILLING_ENABLED):
        return {"skipped": "monthly settlement disabled"}
    now = now or timezone.now()
    beginning, _ = month_bounds(now)
    last_month_start, _ = month_bounds(beginning - timedelta(seconds=1))
    period = _period(last_month_start)
    counts = {"charged": 0, "already_paid": 0, "zero_usage": 0, "insufficient_credits": 0}
    for company in Company.objects.filter(is_active=True).iterator():
        # Old unpaid months must be retried before new charges.
        unpaid = list(MonthlyCreditStatement.objects.filter(
            company=company, status="pending", period__lt=period,
        ).order_by("period").values_list("period", flat=True))
        for due in [*unpaid, period]:
            status, _ = settle_company_month(company, due, now=now)
            if status in counts:
                counts[status] += 1
    return counts
