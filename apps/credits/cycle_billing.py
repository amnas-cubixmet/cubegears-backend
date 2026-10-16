"""Opt-in 5th-to-5th (configurable 1..28) deferred wallet-credit billing.

Independent of old calendar-month, snapshot, prepaid and label renewal engines.
Once active, only this module invoices NEW postpaid label sessions and observed
StorageEvent byte-time. No bank withdrawal, browser-priced usage or negative
wallet balance. A monthly bill becomes due at the next cycle boundary; it is
retried idempotently after top-ups. Paid APIs lock after an unpaid due invoice.
"""
import logging
from calendar import monthrange
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts.permissions import is_company_owner
from apps.companies.models import Company
from apps.saas.models import StorageEvent
from apps.saas.storage_ledger import ensure_baseline
from apps.saas.access import FREE_TENANT_MODULES
from .models import (
    CompanyBundleAccess, CreditCycleInvoice, CreditCyclePolicy,
    CycleLabelSession, FeatureBundle, StorageCreditPolicy,
)
from .service import billable_user_count, change_balance, wallet_for

LOG = logging.getLogger(__name__)
ZERO = Decimal("0")
GB = Decimal(1024 ** 3)
CENT = Decimal("0.01")


def effective_policy():
    from .models import BillingCyclePolicy
    if BillingCyclePolicy.objects.filter(enabled=True).exists():
        return None
    return CreditCyclePolicy.objects.filter(enabled=True).order_by("pk").first()


def _zone():
    return ZoneInfo(settings.TIME_ZONE)


def _advance_month(year, month, step=1):
    value = year * 12 + month - 1 + step
    return value // 12, value % 12 + 1


def cycle_bounds(at, day=5):
    """Return containing [start,end) in backend timezone, for 1 <= day <= 28."""
    if not 1 <= int(day) <= 28:
        raise ValueError("Cycle day must be between 1 and 28.")
    local = timezone.localtime(at, _zone())
    start_y, start_m = local.year, local.month
    if local.day < day:
        start_y, start_m = _advance_month(start_y, start_m, -1)
    end_y, end_m = _advance_month(start_y, start_m)
    return (
        datetime(start_y, start_m, day, tzinfo=_zone()),
        datetime(end_y, end_m, day, tzinfo=_zone()),
    )


def _latest_due_start(at, day):
    start, end = cycle_bounds(at, day)
    if at < end:  # The containing cycle is open. The previous just closed.
        y, m = _advance_month(start.year, start.month, -1)
        old = datetime(y, m, day, tzinfo=_zone())
        return old, start
    return start, end


def cycle_status(company, *, at=None):
    """Read-only access state; *only* a persisted overdue invoice may block."""
    at = at or timezone.now()
    policy = effective_policy()
    if not policy:
        return {"enabled": False, "dayOfMonth": 5, "overdue": False,
                "blockOverdue": False, "amountDue": "0.00", "invoices": []}
    current_start, next_due = cycle_bounds(at, policy.day_of_month)
    qs = CreditCycleInvoice.objects.filter(
        company=company, status__in=("pending", "insufficient"),
    )
    debt = sum((item.credits_due for item in qs), ZERO)
    overdue = policy.block_overdue and qs.filter(
        due_at__lte=at, credits_due__gt=ZERO,
    ).exists()
    recent = CreditCycleInvoice.objects.filter(company=company).order_by(
        "-starts_on", "category",
    )[:48]
    return {
        "enabled": True, "dayOfMonth": policy.day_of_month,
        "blockOverdue": policy.block_overdue,
        "overdue": overdue,
        "amountDue": str(debt.quantize(CENT)),
        "cycleStart": current_start.isoformat(),
        "nextDueAt": next_due.isoformat(),
        "invoices": [{
            "id": row.pk, "category": row.category, "startsOn": row.starts_on.isoformat(),
            "endsAt": row.ends_at.isoformat(), "dueAt": row.due_at.isoformat(),
            "creditsDue": str(row.credits_due), "status": row.status,
            "settledAt": row.settled_at.isoformat() if row.settled_at else None,
            "details": row.details,
        } for row in recent],
    }


def company_is_overdue(company, *, at=None):
    policy = effective_policy()
    if not policy or not policy.block_overdue or not company:
        return False
    return CreditCycleInvoice.objects.filter(
        company=company, status__in=("pending", "insufficient"),
        due_at__lte=at or timezone.now(), credits_due__gt=ZERO,
    ).exists()


def _bytes_cost(used_bytes, policy):
    gb = Decimal(max(used_bytes, 0)) / GB
    return max(ZERO, gb - policy.included_gb) * policy.credits_per_gb


def storage_cycle_quote(company, start, end, policy):
    """Exact observed byte-time from the immutable upload/delete event stream.

    A missing baseline is NEVER filled by guessing past usage. Deletion keeps
    its filename and the billed occupied duration remains in prior cycles.
    """
    storage_policy = StorageCreditPolicy.objects.filter(
        is_active=True, billing_period="monthly",
    ).order_by("pk").first()
    if not storage_policy or storage_policy.credits_per_gb <= ZERO:
        return ZERO, {"events": 0, "fileEvents": [], "policy": "disabled"}
    from_at = max(start, policy.activated_at or end)
    if from_at >= end:
        return ZERO, {"events": 0, "fileEvents": [], "policy": "not_active_yet"}
    total_seconds = Decimal(str((end - start).total_seconds()))
    previous = StorageEvent.objects.filter(
        company=company, occurred_at__lte=from_at,
    ).order_by("-occurred_at", "-created_at", "-id").first()
    bytes_used = previous.storage_after_bytes if previous else 0
    at = from_at
    charge = ZERO
    events = []
    for event in StorageEvent.objects.filter(
        company=company, occurred_at__gt=from_at, occurred_at__lt=end,
    ).order_by("occurred_at", "created_at", "id").iterator():
        seconds = Decimal(str(max(0, (event.occurred_at - at).total_seconds())))
        charge += _bytes_cost(bytes_used, storage_policy) * seconds / total_seconds
        # Snapshot includes filenames even for deletions. Never invent timestamps.
        events.append({
            "filename": event.name_snapshot, "eventType": event.event_type,
            "bytes": event.file_size_bytes, "at": event.occurred_at.isoformat(),
            "afterBytes": event.storage_after_bytes,
        })
        bytes_used = event.storage_after_bytes
        at = event.occurred_at
    charge += _bytes_cost(bytes_used, storage_policy) * Decimal(
        str(max(0, (end - at).total_seconds()))
    ) / total_seconds
    return charge.quantize(CENT, rounding=ROUND_HALF_UP), {
        "events": len(events), "fileEvents": events[:200],
        "moreEvents": len(events) > 200,
        "includedGb": str(storage_policy.included_gb),
        "rateCreditsPerGbCycle": str(storage_policy.credits_per_gb),
        "knownFrom": from_at.isoformat(),
        "endBytes": bytes_used, "pricing": "observed_byte_time",
    }


def feature_cycle_quote(company, start, end, policy):
    from_at = max(start, policy.activated_at or end)
    if from_at >= end:
        return ZERO, {"labels": []}
    length = Decimal(str((end - start).total_seconds()))
    qs = CycleLabelSession.objects.filter(
        company=company, started_at__lt=end,
    ).filter(Q(ended_at__gt=from_at) | Q(ended_at__isnull=True)).select_related("bundle")
    total, items = ZERO, []
    for session in qs:
        if not session.bundle.is_active:
            # An administrator unpublishing a feature stops new entitlements,
            # not a historically used session already recorded.
            pass
        if not session.bundle.features.exclude(key__in=FREE_TENANT_MODULES).exists():
            continue
        opened = max(session.started_at, from_at)
        closed = min(session.ended_at or end, end)
        seconds = Decimal(str(max(0, (closed - opened).total_seconds())))
        if seconds == 0:
            continue
        amount = session.monthly_credits * seconds / length
        total += amount
        items.append({
            "bundleId": session.bundle_id, "label": session.bundle.name,
            "seconds": str(seconds), "userCount": session.user_seats,
            "monthlyRateCredits": str(session.monthly_credits),
            "credits": str(amount.quantize(CENT, rounding=ROUND_HALF_UP)),
        })
    return total.quantize(CENT, rounding=ROUND_HALF_UP), {"labels": items}


@transaction.atomic
def settle_cycle_category(company, category, start, end, *, at=None, dry_run=False):
    at = at or timezone.now()
    policy = effective_policy()
    if not policy:
        return "disabled", None
    if category not in ("feature", "storage"):
        raise ValueError("Invalid cycle billing category.")
    if at < end:
        return "period_open", None
    # No retroactive bills for periods entirely before cycle activation.
    if not policy.activated_at or end <= policy.activated_at:
        return "not_active_yet", None
    row = CreditCycleInvoice.objects.select_for_update().filter(
        company=company, category=category, starts_on=start.date(),
    ).first()
    if row and row.status == "paid":
        return "already_paid", row
    if not row:
        if category == "storage":
            amount, details = storage_cycle_quote(company, start, end, policy)
        else:
            amount, details = feature_cycle_quote(company, start, end, policy)
        if dry_run:
            return "would_charge", {"amount": str(amount), "details": details}
        row = CreditCycleInvoice.objects.create(
            company=company, category=category, starts_on=start.date(),
            ends_at=end, due_at=end,
            credits_due=amount, details=details, status="pending",
        )
    if dry_run:
        return "would_charge", {"amount": str(row.credits_due), "details": row.details}
    if row.credits_due == ZERO:
        row.status = "paid"
        row.settled_at = at
        row.save(update_fields=["status", "settled_at"])
        return "zero_usage", row
    wallet_for(company)
    try:
        change_balance(
            company, -row.credits_due, kind=f"{category}_charge",
            reference=f"credit-cycle:{category}:{company.pk}:{start.date().isoformat()}",
            description=f"Postpaid {category} {start.date()} to {end.date()}: {row.credits_due} credits",
        )
    except ValueError:
        row.status = "insufficient"
        row.save(update_fields=["status"])
        return "insufficient", row
    row.status = "paid"
    row.settled_at = at
    row.save(update_fields=["status", "settled_at"])
    return "paid", row


def run_due_cycles(*, at=None, dry_run=False):
    """Single worker/hourly retry. Idempotent even when the worker runs twice."""
    at = at or timezone.now()
    policy = effective_policy()
    if not policy:
        return {"disabled": 1}
    result = {}
    # Cover all missed cycles after the enable date, bounded to 24 months.
    start, end = cycle_bounds(policy.activated_at, policy.day_of_month)
    cycles = []
    for _ in range(24):
        if end > at:
            break
        cycles.append((start, end))
        start = end
        y, m = _advance_month(start.year, start.month)
        end = datetime(y, m, policy.day_of_month, tzinfo=_zone())
    for company in Company.objects.filter(is_active=True).iterator():
        if not dry_run:
            try:
                reconcile_cycle_label_seats(company, at=at)
            except Exception:
                LOG.exception("Cycle seat reconciliation failed company=%s", company.pk)
                result["seat_errors"] = result.get("seat_errors", 0) + 1
        # Preserve lock order: storage and features debit the *same* wallet.
        for cycle_start, cycle_end in cycles:
            for category in ("feature", "storage"):
                try:
                    outcome, _ = settle_cycle_category(
                        company, category, cycle_start, cycle_end,
                        at=at, dry_run=dry_run,
                    )
                except Exception:
                    outcome = "error"
                    LOG.exception("Credit cycle failed: company=%s cycle=%s category=%s",
                                  company.pk, cycle_start.date(), category)
                result[outcome] = result.get(outcome, 0) + 1
    return result


@transaction.atomic
def reconcile_cycle_label_seats(company, *, at=None):
    """Close/open exact timestamped rate snapshots when login seat count changes.

    Samples run hourly with the cycle worker. We never rewrite past usage
    rows or charge a seat rate that was not recorded by the server.
    """
    policy = effective_policy()
    if not policy:
        return 0
    at = at or timezone.now()
    users = billable_user_count(company)
    changed = 0
    active = CycleLabelSession.objects.select_for_update().filter(
        company=company, ended_at__isnull=True,
    ).select_related("bundle")
    for session in active:
        entitlement = CompanyBundleAccess.objects.filter(
            company=company, bundle_id=session.bundle_id, enabled=True,
        ).exists()
        if not entitlement or not session.bundle.is_active:
            session.ended_at = at
            session.save(update_fields=["ended_at"])
            changed += 1
            continue
        new_rate = (session.bundle.per_user_credits * users).quantize(CENT)
        if new_rate == session.monthly_credits and users == session.user_seats:
            continue
        session.ended_at = at
        session.save(update_fields=["ended_at"])
        CycleLabelSession.objects.create(
            company=company, bundle=session.bundle, started_at=at,
            monthly_credits=new_rate, user_seats=users,
        )
        changed += 1
    return changed


@transaction.atomic
def activate_cycle_label(company, bundle, actor):
    policy = effective_policy()
    if not policy:
        raise ValueError("Postpaid cycle billing is not enabled.")
    if company_is_overdue(company):
        raise ValueError("Overdue wallet bill: add credits and settle it before activating modules.")
    if bundle.billing_type != "monthly" or not bundle.is_active:
        raise ValueError("Only published monthly labels can use postpaid cycles.")
    if not bundle.features.filter(is_active=True).exclude(key__in=FREE_TENANT_MODULES).exists():
        raise ValueError("Free-only labels do not require activation.")
    if not Decimal("0.01") <= bundle.per_user_credits <= Decimal("1000000"):
        raise ValueError("Valid platform per-user pricing is required.")
    existing = CompanyBundleAccess.objects.select_for_update().filter(
        company=company, bundle=bundle,
    ).first()
    if existing and existing.enabled and not CycleLabelSession.objects.filter(
        company=company, bundle=bundle, ended_at__isnull=True,
    ).exists():
        raise ValueError("Existing prepaid access requires administrator migration before switching to postpaid.")
    if existing and existing.enabled:
        return existing, ZERO
    users = billable_user_count(company)
    amount = (bundle.per_user_credits * users).quantize(CENT)
    if amount <= ZERO:
        raise ValueError("A positive server-verified seat price is required.")
    now = timezone.now()
    access, _ = CompanyBundleAccess.objects.get_or_create(
        company=company, bundle=bundle, defaults={"enabled": False},
    )
    if access.last_price_credits > ZERO and (
        access.valid_until is None or access.valid_until > now
    ):
        raise ValueError("Existing prepaid access must expire before postpaid activation.")
    CycleLabelSession.objects.create(
        company=company, bundle=bundle, started_at=now,
        monthly_credits=amount, user_seats=users,
    )
    access.enabled = True
    access.valid_until = None
    access.billing_type_at_purchase = "permanent"  # access lifetime is debt-gated
    access.last_price_credits = ZERO
    access.purchased_at = now
    access.save(update_fields=[
        "enabled", "valid_until", "billing_type_at_purchase",
        "last_price_credits", "purchased_at", "updated_at",
    ])
    from apps.saas.models import SecurityEvent
    SecurityEvent.objects.create(company=company, user=actor,
        event="feature_label_cycle_activated", metadata={
            "bundleId": bundle.pk, "userCount": users,
            "rateCreditsPerCycle": str(amount), "cycleDay": policy.day_of_month,
        })
    return access, ZERO


def stop_cycle_label(company, bundle, *, at=None):
    if not effective_policy():
        return
    CycleLabelSession.objects.filter(
        company=company, bundle=bundle, ended_at__isnull=True,
    ).update(ended_at=at or timezone.now())
