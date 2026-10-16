"""Opt-in monthly postpaid credit accounting.

Rates are wallet credits, NOT Indian rupees. Storage samples are a bounded
approximation, not a perfect reconstruction of uploads during missing ticks.
Never bill if a source has no verifiable records for its usage period.
"""
import calendar
import logging
from datetime import datetime, timedelta, timezone as utc_timezone
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from apps.companies.models import Company
from apps.saas.access import FREE_TENANT_MODULES
from .models import (
    CreditLedger, FeatureUsageSession, MonthlyUsageInvoice,
    StorageCreditPolicy, StorageUsageInterval,
)
from .service import wallet_for, change_balance
from .storage_billing import storage_usage

logger = logging.getLogger(__name__)
ZERO = Decimal("0")
GB = Decimal(1024 ** 3)


def _timezone():
    return ZoneInfo(settings.TIME_ZONE)


def month_bounds(month):
    try:
        year, number = [int(x) for x in month.split("-")]
        if f"{year:04d}-{number:02d}" != month or not 1 <= number <= 12:
            raise ValueError()
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("Billing month must be YYYY-MM.") from exc
    zone = _timezone()
    start = datetime(year, number, 1, tzinfo=zone)
    end = datetime(year + (number == 12), 1 if number == 12 else number + 1, 1, tzinfo=zone)
    return start, end


def completed_month(now=None):
    now = now or timezone.now()
    this_month_start = timezone.localtime(now, _timezone()).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    last_day = this_month_start - timedelta(days=1)
    return last_day.strftime("%Y-%m")


def feature_month_quote(company, month):
    start, end = month_bounds(month)
    length = Decimal(str(end.astimezone(utc_timezone.utc).timestamp() -
                         start.astimezone(utc_timezone.utc).timestamp()))
    sessions = FeatureUsageSession.objects.filter(
        company=company, started_at__lt=end,
    ).filter(Q(ended_at__gt=start) | Q(ended_at__isnull=True))
    due = ZERO
    rows = []
    for session in sessions.select_related("feature"):
        # Retired paid sessions for now-free modules remain as audit records,
        # but may never create a new debit after the free policy is deployed.
        if session.feature.key in FREE_TENANT_MODULES:
            continue
        opened = max(start, session.started_at)
        closed = min(end, session.ended_at or end)
        seconds = Decimal(str(max(0, (closed-opened).total_seconds())))
        month_charge = session.monthly_credits * seconds / length
        activation = session.activation_credits if start <= session.started_at < end else ZERO
        due += month_charge + activation
        rows.append({
            "feature": session.feature.key,
            "session": session.pk,
            "seconds": str(seconds),
            "monthlyCredits": str(session.monthly_credits),
            "activationCredits": str(activation),
            "users": session.user_seats,
            "employees": session.staff_seats,
        })
    return due.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), rows


@transaction.atomic
def record_storage_interval(company, *, now=None, policy=None, mutation=False):
    """Record at most one interval due per call and never extrapolate downtime."""
    now = now or timezone.now()
    policy = policy or StorageCreditPolicy.objects.filter(is_active=True, billing_mode="metered_monthly").first()
    if not policy or not policy.is_active or policy.billing_mode != "metered_monthly":
        return "disabled", None
    interval = policy.meter_interval_seconds
    if interval < 60 or interval > 86400:
        raise ValueError("Storage metering interval must be 60 to 86400 seconds.")
    if policy.credits_per_gb < 0 or policy.included_gb < 0:
        raise ValueError("Storage pricing values cannot be negative.")
    wallet_for(company)
    # A company-level wallet lock serializes ticks with month-end debits.
    from .models import CreditWallet
    CreditWallet.objects.select_for_update().get(company=company)
    last = StorageUsageInterval.objects.filter(company=company).order_by("-ends_at").first()
    if last and not mutation and now < last.ends_at + timedelta(seconds=interval):
        return "not_due", last
    month = timezone.localtime(now, _timezone()).strftime("%Y-%m")
    month_start, month_end = month_bounds(month)
    period_seconds = Decimal(str(
        month_end.astimezone(utc_timezone.utc).timestamp() -
        month_start.astimezone(utc_timezone.utc).timestamp()
    ))
    # Never assume bytes during a missed interval or before policy activation.
    start = max(now - timedelta(seconds=interval), policy.updated_at, month_start)
    if last and last.ends_at > start:
        start = last.ends_at
    if start >= now:
        return "not_due", last
    # Storage size at the end of the interval is an estimate of its occupancy.
    bytes_used = storage_usage(company)
    gb = Decimal(bytes_used) / GB
    billable_gb = max(ZERO, gb - policy.included_gb)
    seconds = Decimal(str((now - start).total_seconds()))
    amount = (billable_gb * policy.credits_per_gb * seconds / period_seconds).quantize(
        Decimal("0.000001"), rounding=ROUND_HALF_UP,
    )
    # Reject late writes after a monthly invoice has been paid; never silently
    # modify an already-settled period.
    if MonthlyUsageInvoice.objects.filter(
        company=company, category="storage", billing_month=month, status="paid",
    ).exists():
        return "already_settled", None
    item, created = StorageUsageInterval.objects.get_or_create(
        company=company, ends_at=now,
        defaults={
            "starts_at": start, "billing_month": month, "measured_bytes": bytes_used,
            "credits_accrued": amount, "rate_per_gb_month": policy.credits_per_gb,
        },
    )
    if last and now - last.ends_at > timedelta(seconds=interval * 2):
        logger.warning("Storage metering gap: company=%s from=%s to=%s", company.pk, last.ends_at, now)
    return ("metered" if created else "already_metered"), item


@transaction.atomic
def refresh_postpaid_feature_rates(company, *, now=None):
    """Split active sessions when billable login seats or staff counts change.

    Runs on a short Celery schedule, not on untrusted browser notifications.
    A new segment preserves the previous period's unit price and quantity.
    """
    if (not settings.CREDIT_POSTPAID_FEATURE_BILLING_ENABLED
            or getattr(settings, "CREDIT_LABEL_ONLY_ACCESS_ENABLED", False)):
        return 0
    from .models import CompanyCreditFeature, FeatureCreditPrice, CreditWallet
    from .service import staff_count, billable_user_count, price_for
    now = now or timezone.now()
    wallet_for(company)
    CreditWallet.objects.select_for_update().get(company=company)
    users = billable_user_count(company)
    staff = staff_count(company)
    changed = 0
    for state in CompanyCreditFeature.objects.filter(
        company=company, enabled=True,
    ).select_related("feature").select_for_update():
        if state.feature.key in FREE_TENANT_MODULES:
            continue
        open_session = FeatureUsageSession.objects.filter(
            company=company, feature=state.feature, ended_at__isnull=True,
        ).first()
        # Never silently convert a legacy prepaid entitlement into postpaid.
        if not open_session:
            logger.error("Postpaid feature session missing: company=%s feature=%s",
                         company.pk, state.feature.key)
            continue
        price = FeatureCreditPrice.objects.filter(
            feature=state.feature, is_available=True, feature__is_active=True,
            billing_period="monthly",
        ).first()
        if not price:
            logger.error("No valid monthly rate for postpaid company=%s feature=%s",
                         company.pk, state.feature.key)
            continue
        new_rate = price_for(price, staff, users)
        if (open_session.user_seats == users and open_session.staff_seats == staff
                and open_session.monthly_credits == new_rate):
            continue
        open_session.ended_at = now
        open_session.save(update_fields=["ended_at"])
        FeatureUsageSession.objects.create(
            company=company, feature=state.feature, started_at=now,
            monthly_credits=new_rate, activation_credits=ZERO,
            user_seats=users, staff_seats=staff,
        )
        changed += 1
    return changed


def storage_month_quote(company, month):
    # These are real rows created by a backend meter; the browser cannot submit
    # usage or price. No rows means no auditable basis for an invoice.
    entries = StorageUsageInterval.objects.filter(company=company, billing_month=month)
    count = entries.count()
    total = entries.aggregate(total=Sum("credits_accrued"))["total"] or ZERO
    return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), count


@transaction.atomic
def settle_monthly_usage(company, category, month, *, now=None):
    """Atomic wallet debit, one category/company/month; retry insufficient funds."""
    now = now or timezone.now()
    from .calendar_billing import active_policy
    if active_policy():
        return "calendar_policy", None
    if category not in {"feature", "storage"}:
        raise ValueError("Unknown monthly billing category.")
    _, end = month_bounds(month)
    if end > now:
        raise ValueError("Cannot settle a month that has not ended.")
    if category == "feature":
        # Label-only entitlements must not auto-debit hidden standalone sessions.
        # Keep historical readings for an explicit administrator reconciliation.
        if getattr(settings, "CREDIT_LABEL_ONLY_ACCESS_ENABLED", False):
            return "label_only", None
        if not settings.CREDIT_POSTPAID_FEATURE_BILLING_ENABLED:
            return "disabled", None
        amount, details = feature_month_quote(company, month)
        if not details:
            return "no_usage", None
        metadata = {"sessions": details}
    else:
        policy = StorageCreditPolicy.objects.filter(
            is_active=True, billing_mode="metered_monthly"
        ).first()
        if not policy:
            return "disabled", None
        amount, count = storage_month_quote(company, month)
        if not count:
            return "no_samples", None
        metadata = {"samples": count, "approximateUsage": True}

    wallet_for(company)
    from .models import CreditWallet
    CreditWallet.objects.select_for_update().get(company=company)
    if category == "storage" and CreditLedger.objects.filter(
        company=company, reference=f"storage:{company.pk}:monthly:{month}",
    ).exists():
        return "already_snapshot_billed", None
    invoice, _ = MonthlyUsageInvoice.objects.get_or_create(
        company=company, category=category, billing_month=month,
    )
    invoice = MonthlyUsageInvoice.objects.select_for_update().get(pk=invoice.pk)
    if invoice.status == "paid":
        return "already_paid", invoice
    invoice.credits_due = amount
    invoice.details = metadata
    from .monthly_policy import collection_waiting
    if collection_waiting(month, now):
        invoice.status = 'pending'
        invoice.save(update_fields=['credits_due', 'details', 'status'])
        return 'not_due', invoice
    reference = f"usage:{category}:{company.pk}:{month}"
    try:
        change_balance(
            company, -amount, kind=f"{category}_charge",
            reference=reference,
            description=f"{category.title()} usage {month}: {amount} wallet credits",
        )
    except ValueError:
        invoice.status = "insufficient"
        invoice.save(update_fields=["credits_due", "details", "status"])
        from .billing_notices import record_billing_notice
        record_billing_notice(company, category, month, "insufficient", amount)
        logger.warning("Insufficient wallet credit for company=%s, category=%s, period=%s",
                       company.pk, category, month)
        return "insufficient", invoice
    invoice.status = "paid"
    invoice.settled_at = now
    invoice.save(update_fields=["credits_due", "details", "status", "settled_at"])
    from .billing_notices import record_billing_notice
    record_billing_notice(company, category, month, "paid", amount)
    return "paid", invoice


def run_monthly_settlement(*, now=None):
    now = now or timezone.now()
    period = completed_month(now)
    totals = {}
    for company in Company.objects.filter(is_active=True).iterator():
        for category in ("feature", "storage"):
            try:
                state, _ = settle_monthly_usage(company, category, period, now=now)
                totals[state] = totals.get(state, 0) + 1
            except Exception:
                logger.exception("Monthly billing failed company=%s category=%s month=%s",
                                 company.pk, category, period)
                totals["errors"] = totals.get("errors", 0) + 1
    return totals
