"""Opt-in, server-authoritative calendar-month credit settlement.

Measured usage is immutable once finalized. No balance is taken before the
configured due day. One atomic wallet debit and one unique ledger reference
settle a company/month, avoiding category-level partial payment. Legacy
settlement tasks MUST be gated off while this policy is enabled.
"""
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from apps.companies.models import Company
from apps.saas.access import FREE_TENANT_MODULES
from .models import (
    BillingCyclePolicy, BillingCycleStatement, CreditLedger, CreditWallet,
    FeatureMeterPeriod, FeatureMeterReading, CompanyPostpaidFeature,
    StorageMeterCursor, StorageMeterPeriod, StorageCreditPolicy,
)
from .service import wallet_for, change_balance
from .monthly_storage import _month_bounds, _period_for

ZERO = Decimal("0.00")
CENT = Decimal("0.01")


def active_policy():
    return BillingCyclePolicy.objects.filter(enabled=True).order_by("pk").first()


def deadline_for(month, due_day):
    """5th 00:05 local is debit time; overdue starts on the 6th local."""
    _, end = _month_bounds(month)
    local = timezone.localtime(end, timezone.get_default_timezone())
    return local.replace(day=due_day, hour=0, minute=5, second=0, microsecond=0)


def _ready(company, month, at):
    _, end = _month_bounds(month)
    if at < end:
        return False
    if settings.STORAGE_MONTHLY_METERED_ENABLED:
        policy = StorageCreditPolicy.objects.filter(
            is_active=True, billing_period="monthly", billing_mode="snapshot",
        ).first()
        if policy:
            cursor = StorageMeterCursor.objects.filter(company=company).first()
            if not cursor or not cursor.sampled_at or cursor.sampled_at < end:
                return False
    if settings.CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED:
        running = CompanyPostpaidFeature.objects.filter(
            company=company, enabled=True, suspended=False,
        )
        if running.filter(sampled_at__lt=end).exists() or running.filter(sampled_at__isnull=True).exists():
            return False
    return True


def _amounts(company, month):
    storage = ZERO
    features = ZERO
    breakdown = []
    if settings.STORAGE_MONTHLY_METERED_ENABLED:
        for row in StorageMeterPeriod.objects.filter(company=company, period=month, settled_at__isnull=True):
            ref = f"storage-metered:{company.pk}:{month}"
            legacy_ref = f"storage:{company.pk}:monthly:{month}"
            if not CreditLedger.objects.filter(company=company, reference__in=[ref, legacy_ref]).exists():
                storage += row.accrued_credits
                breakdown.append({"kind": "storage", "coveredSeconds": row.covered_seconds,
                                  "credits": str(row.accrued_credits)})
    if settings.CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED and not settings.CREDIT_LABEL_ONLY_ACCESS_ENABLED:
        for row in FeatureMeterPeriod.objects.filter(
            company=company, period=month, settled_at__isnull=True,
        ).select_related("feature"):
            if row.feature.key in FREE_TENANT_MODULES:
                continue
            ref = f"feature-metered:{company.pk}:{row.feature_id}:{month}"
            if CreditLedger.objects.filter(company=company, reference=ref).exists():
                continue
            amount = row.accrued_credits + row.activation_credits
            features += amount
            breakdown.append({"kind": "feature", "feature": row.feature.key,
                              "coveredSeconds": row.covered_seconds, "credits": str(amount)})
    return (
        features.quantize(CENT, rounding=ROUND_HALF_UP),
        storage.quantize(CENT, rounding=ROUND_HALF_UP),
        breakdown,
    )


@transaction.atomic
def finalize_month(company, month, *, at=None):
    policy = active_policy()
    if not policy:
        return "disabled", None
    at = at or timezone.now()
    if (policy.effective_month and month < policy.effective_month):
        return "before_activation", None
    existing = BillingCycleStatement.objects.select_for_update().filter(company=company, month=month).first()
    if existing:
        return "already_finalized", existing
    if not _ready(company, month, at):
        return "awaiting_meter", None
    feature, storage, details = _amounts(company, month)
    statement = BillingCycleStatement.objects.create(
        company=company, month=month, due_at=deadline_for(month, policy.due_day),
        feature_credits=feature, storage_credits=storage, total_credits=feature + storage,
        details={"items": details}, finalized_at=at,
    )
    return "finalized", statement


@transaction.atomic
def settle_statement(statement, *, at=None):
    at = at or timezone.now()
    policy = active_policy()
    if not policy:
        return "disabled", statement
    # The settled flag and unique reference are both required for retries.
    statement = BillingCycleStatement.objects.select_for_update().get(pk=statement.pk)
    if statement.status == "paid":
        return "already_paid", statement
    if at < statement.due_at:
        return "not_due", statement
    if statement.total_credits < ZERO:
        raise ValueError("Negative usage credits cannot be settled.")
    wallet_for(statement.company)
    CreditWallet.objects.select_for_update().get(company=statement.company)
    ref = f"calendar-usage:{statement.company_id}:{statement.month}"
    if statement.total_credits > ZERO:
        try:
            change_balance(
                statement.company, -statement.total_credits,
                kind="usage_charge", reference=ref,
                description=f"Calendar usage {statement.month}: feature + storage",
            )
        except ValueError:
            statement.status = "insufficient"
            statement.save(update_fields=["status"])
            return "insufficient", statement
    else:
        # Zero-usage periods are paid without creating a 0-credit ledger debit.
        pass
    statement.status = "paid"
    statement.settled_at = at
    statement.save(update_fields=["status", "settled_at"])
    # Existing metered periods stay visible as historical records; mark them
    # settled so their legacy schedulers cannot charge the same usage again.
    FeatureMeterPeriod.objects.filter(
        company=statement.company, period=statement.month,
        settled_at__isnull=True,
    ).update(settled_at=at)
    StorageMeterPeriod.objects.filter(
        company=statement.company, period=statement.month,
        settled_at__isnull=True,
    ).update(settled_at=at)
    return "paid", statement


def run_calendar_cycles(*, at=None, dry_run=False):
    at = at or timezone.now()
    policy = active_policy()
    if not policy:
        return {"disabled": 1}
    current = _period_for(at)
    months = set()
    if settings.STORAGE_MONTHLY_METERED_ENABLED:
        months.update(StorageMeterPeriod.objects.filter(period__lt=current).values_list("period", flat=True))
    if settings.CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED:
        months.update(FeatureMeterPeriod.objects.filter(period__lt=current).values_list("period", flat=True))
    months.update(BillingCycleStatement.objects.exclude(status="paid").values_list("month", flat=True))
    report = {}
    for month in sorted(months):
        if policy.effective_month and month < policy.effective_month:
            continue
        ids = set(StorageMeterPeriod.objects.filter(period=month).values_list("company_id", flat=True))
        ids.update(FeatureMeterPeriod.objects.filter(period=month).values_list("company_id", flat=True))
        ids.update(BillingCycleStatement.objects.filter(month=month).values_list("company_id", flat=True))
        for company in Company.objects.filter(pk__in=ids, is_active=True).iterator():
            if dry_run:
                status = "would_finalize" if _ready(company, month, at) else "awaiting_meter"
            else:
                status, statement = finalize_month(company, month, at=at)
                if statement:
                    status, _ = settle_statement(statement, at=at)
            report[status] = report.get(status, 0) + 1
    return report


def overdue_statements(company, *, at=None):
    policy = active_policy()
    if not policy or not policy.restrict_overdue:
        return []
    at = at or timezone.now()
    # Grace extends to end-of-day on the configured due day (local time).
    today = timezone.localtime(at, timezone.get_default_timezone()).date()
    result = []
    for row in BillingCycleStatement.objects.filter(company=company).exclude(status="paid").order_by("month"):
        if today > timezone.localtime(row.due_at, timezone.get_default_timezone()).date():
            if row.total_credits > ZERO:
                result.append(row)
    return result


def billing_status(company, *, at=None):
    at = at or timezone.now()
    policy = active_policy()
    rows = BillingCycleStatement.objects.filter(company=company).order_by("-month")[:24]
    overdue = overdue_statements(company, at=at)
    start, _ = _month_bounds(_period_for(at))
    pending_total = sum((s.total_credits for s in rows if s.status != "paid"), ZERO)
    return {
        "enabled": bool(policy), "dueDay": policy.due_day if policy else 5,
        "dayOfMonth": 1,
        "cycleStart": start.isoformat(),
        "nextDueAt": min(
            (s.due_at for s in rows if s.status != "paid"),
            default=deadline_for(_period_for(at), policy.due_day if policy else 5),
        ).isoformat(),
        "amountDue": str(pending_total),
        "overdue": bool(overdue),
        "invoices": [{
            "id": s.pk, "startsOn": s.month + "-01",
            "dueAt": s.due_at.isoformat(), "category": "combined",
            "creditsDue": str(s.total_credits), "status": s.status,
            "details": s.details,
        } for s in rows],
        "allowZeroBalanceUsage": policy.allow_zero_balance_usage if policy else False,
        "restrictOverdue": policy.restrict_overdue if policy else False,
        "restricted": bool(overdue),
        "overdueCredits": str(sum((s.total_credits for s in overdue), ZERO)),
        "currentMonth": _period_for(at),
        "statements": [{
            "id": s.pk, "month": s.month, "dueAt": s.due_at.isoformat(),
            "featureCredits": str(s.feature_credits),
            "storageCredits": str(s.storage_credits),
            "totalCredits": str(s.total_credits), "status": s.status,
            "settledAt": s.settled_at.isoformat() if s.settled_at else None,
            "details": s.details,
        } for s in rows],
    }
