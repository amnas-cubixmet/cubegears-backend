"""Explicit opt-in postpaid feature billing by time, active users and staff.

Prepaid legacy feature access remains unchanged. Only new postpaid entitlements
are measured here. Fees represent CREDITs per calendar month (not INR).
"""
import logging
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.companies.models import Company
from .models import (
    CompanyCreditFeature, CompanyPostpaidFeature, CreditLedger,
    FeatureCreditPrice, FeatureMeterPeriod, FeatureMeterReading,
)
from .monthly_storage import _month_bounds, _period_for
from .service import (
    billable_user_count, change_balance, price_for, staff_count, wallet_for,
)

LOG = logging.getLogger(__name__)
ZERO = Decimal("0")
PRECISION = Decimal("0.00000001")
CENTS = Decimal("0.01")


def _enabled():
    # Never accrue/sell postpaid credits when tenant feature enforcement is off.
    return bool(
        settings.CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED
        and settings.CREDIT_FEATURE_ENFORCEMENT_ENABLED
    )


def _rate(company, feature):
    price = FeatureCreditPrice.objects.filter(
        feature=feature, is_available=True, feature__is_active=True,
        billing_period="monthly",
    ).first()
    if price is None:
        raise ValueError("This feature has no available monthly pricing.")
    users = billable_user_count(company)
    staff = staff_count(company)
    amount = price_for(price, staff, users)
    if amount < ZERO or price.activation_credits < ZERO:
        raise ValueError("Negative feature prices are not allowed.")
    return price, amount, users, staff


@transaction.atomic
def activate_postpaid_feature(company, feature, *, at=None):
    if not _enabled():
        raise ValueError("Monthly postpaid feature billing is disabled.")
    previous = CompanyCreditFeature.objects.filter(company=company, feature=feature).first()
    if previous and previous.enabled:
        raise ValueError("Turn off the existing prepaid feature before changing its billing model.")
    # A disabled prepaid feature may still be inside the period it already
    # paid for; don't charge postpaid for the same time.
    if previous:
        from .service import BILLING_DURATIONS
        rate_info = FeatureCreditPrice.objects.filter(feature=feature).first()
        duration = BILLING_DURATIONS.get(rate_info.billing_period) if rate_info else None
        if duration:
            from django.db.models import Q
            recent = CreditLedger.objects.filter(
                company=company, kind="feature_charge",
                created_at__gte=(at or timezone.now()) - duration,
            ).filter(
                Q(reference__startswith=f"feature-start:{previous.pk}:") |
                Q(reference__startswith=f"feature-renew:{previous.pk}:")
            ).exists()
            if recent:
                raise ValueError("The already-paid prepaid period must expire before starting monthly postpaid billing.")
    # Never sell overlapping paid bundle access and individual postpaid access.
    from .models import CompanyBundleAccess
    from .service import bundle_is_usable
    for entitlement in CompanyBundleAccess.objects.filter(
        company=company, enabled=True, bundle__features=feature,
    ).select_related("bundle").distinct():
        if bundle_is_usable(entitlement):
            raise ValueError("This module already belongs to an active paid package.")
    price, rate, users, staff = _rate(company, feature)
    at = at or timezone.now()
    item, _ = CompanyPostpaidFeature.objects.get_or_create(company=company, feature=feature)
    item = CompanyPostpaidFeature.objects.select_for_update().get(pk=item.pk)
    if item.enabled and not item.suspended:
        return item, ZERO
    if item.suspended:
        raise ValueError("Outstanding monthly feature charges must settle before reactivation.")

    month = _period_for(at)
    period, _ = FeatureMeterPeriod.objects.select_for_update().get_or_create(
        company=company, feature=feature, period=month,
    )
    if period.settled_at:
        raise ValueError("Cannot activate into a finalized billing period.")
    if period.activation_credits == ZERO:
        period.activation_credits = price.activation_credits
        period.save(update_fields=["activation_credits", "updated_at"])

    item.enabled = True
    item.suspended = False
    item.sampled_at = at
    item.monthly_rate = rate
    item.save(update_fields=["enabled", "suspended", "sampled_at", "monthly_rate", "updated_at"])
    FeatureMeterReading.objects.create(
        company=company, feature=feature, sampled_at=at,
        users=users, staff=staff, monthly_rate=rate,
        covered_seconds=0, accrued_credits=ZERO,
    )
    return item, ZERO  # No charge until after month-end.


@transaction.atomic
def meter_postpaid_feature(item, *, at=None):
    if not _enabled():
        return "disabled", None
    at = at or timezone.now()
    if at > timezone.now() + timedelta(minutes=1):
        raise ValueError("Future meter timestamp is invalid.")
    state = CompanyPostpaidFeature.objects.select_for_update().get(pk=item.pk)
    if not state.enabled or state.suspended:
        return "inactive", None
    if state.sampled_at and at <= state.sampled_at:
        return "already_metered", None

    old = state.sampled_at
    month_cost = ZERO
    covered = 0
    if old:
        elapsed = int((at - old).total_seconds())
        max_gap = settings.STORAGE_METER_INTERVAL_SECONDS * 2
        start = old if elapsed <= max_gap else at
        if elapsed > max_gap:
            LOG.warning(
                "Feature metering gap company=%s feature=%s: %s seconds not billed",
                state.company_id, state.feature_id, elapsed - max_gap,
            )
        while start < at:
            period_name = _period_for(start)
            _, end = _month_bounds(period_name)
            segment_end = min(end, at)
            seconds = int((segment_end - start).total_seconds())
            if seconds <= 0:
                break
            begin, finish = _month_bounds(period_name)
            month_seconds = int((finish - begin).total_seconds())
            amount = (state.monthly_rate * Decimal(seconds) / Decimal(month_seconds)).quantize(
                PRECISION, rounding=ROUND_HALF_UP,
            )
            month_row, _ = FeatureMeterPeriod.objects.select_for_update().get_or_create(
                company=state.company, feature=state.feature, period=period_name,
            )
            if month_row.settled_at:
                raise ValueError("Cannot accrue in a settled feature billing month.")
            month_row.accrued_credits += amount
            month_row.covered_seconds += seconds
            month_row.save(update_fields=["accrued_credits", "covered_seconds", "updated_at"])
            month_cost += amount
            covered += seconds
            start = segment_end

    try:
        _, next_rate, users, staff = _rate(state.company, state.feature)
    except ValueError:
        next_rate, users, staff = ZERO, 0, 0
        state.suspended = True
    state.sampled_at = at
    state.monthly_rate = next_rate
    state.save(update_fields=["sampled_at", "monthly_rate", "suspended", "updated_at"])
    reading = FeatureMeterReading.objects.create(
        company=state.company, feature=state.feature, sampled_at=at,
        users=users, staff=staff, monthly_rate=next_rate,
        covered_seconds=covered, accrued_credits=month_cost,
    )
    return "metered", reading


@transaction.atomic
def deactivate_postpaid_feature(company, feature, *, at=None):
    state = CompanyPostpaidFeature.objects.select_for_update().filter(
        company=company, feature=feature,
    ).first()
    if not state:
        return None
    if state.enabled and not state.suspended:
        meter_postpaid_feature(state, at=at)
    state.enabled = False
    state.save(update_fields=["enabled", "updated_at"])
    return state


def meter_all_postpaid_features(*, at=None):
    if not _enabled():
        return {"disabled": 1}
    result = {"metered": 0, "failed": 0}
    for item in CompanyPostpaidFeature.objects.filter(enabled=True, suspended=False).iterator():
        try:
            status, _ = meter_postpaid_feature(item, at=at)
            if status == "metered":
                result["metered"] += 1
        except Exception:
            result["failed"] += 1
            LOG.exception("Feature metering failed for state %s", item.pk)
    return result


@transaction.atomic
def settle_postpaid_feature(company, feature, period, *, at=None, dry_run=False):
    if not _enabled():
        return "disabled", ZERO
    at = at or timezone.now()
    _, end = _month_bounds(period)
    if at < end:
        return "period_open", ZERO
    item = CompanyPostpaidFeature.objects.select_for_update().filter(
        company=company, feature=feature,
    ).first()
    if item is None:
        return "not_found", ZERO
    if item.enabled and not item.suspended and (item.sampled_at is None or item.sampled_at < end):
        return "awaiting_meter", ZERO
    row = FeatureMeterPeriod.objects.select_for_update().filter(
        company=company, feature=feature, period=period,
    ).first()
    if row is None:
        return "no_usage", ZERO
    credits = (row.accrued_credits + row.activation_credits).quantize(
        CENTS, rounding=ROUND_HALF_UP,
    )
    if row.settled_at:
        return "already_billed", credits
    ref = f"feature-metered:{company.pk}:{feature.pk}:{period}"
    if dry_run:
        return "would_charge", credits
    wallet_for(company)
    try:
        _, created = change_balance(
            company, -credits, kind="feature_charge", reference=ref,
            description=f"Postpaid feature {feature.key} {period}: {row.covered_seconds} seconds + activation fee",
        )
    except ValueError:
        item.suspended = True
        item.save(update_fields=["suspended", "updated_at"])
        return "insufficient_credits", credits
    row.settled_at = at
    row.save(update_fields=["settled_at", "updated_at"])
    # Only restore service after ALL earlier feature invoices have settled.
    has_unpaid = FeatureMeterPeriod.objects.filter(
        company=company, feature=feature,
        period__lt=_period_for(at), settled_at__isnull=True,
    ).exists()
    if item.enabled and not has_unpaid and item.suspended:
        item.suspended = False
        item.sampled_at = at  # Do not back-bill downtime while suspended.
        item.save(update_fields=["suspended", "sampled_at", "updated_at"])
    return ("charged" if created else "already_billed"), credits


def settle_due_postpaid_features(*, at=None, dry_run=False):
    if not _enabled():
        return {"disabled": 1}
    at = at or timezone.now()
    current_month = _period_for(at)
    result = {}
    qs = FeatureMeterPeriod.objects.filter(
        period__lt=current_month, settled_at__isnull=True,
    ).select_related("company", "feature").order_by("period", "company_id", "feature_id")
    for row in qs.iterator():
        try:
            state, _ = settle_postpaid_feature(
                row.company, row.feature, row.period, at=at, dry_run=dry_run,
            )
        except Exception:
            state = "failed"
            LOG.exception("Feature settlement failed for %s / %s", row.company_id, row.feature_id)
        result[state] = result.get(state, 0) + 1
    return result
