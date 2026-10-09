import uuid
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.attendance.models import AttendanceRecord
from apps.employees.models import Employee
from .daily_wage_models import (
    DailyWageEntry, DailyWageExtra, EmployeeDailyWageRate,
    WageAdjustment, WageAuditLog, WagePayment, WagePaymentAllocation, WagePaymentReversal,
)

ZERO = Decimal("0.00")
CENT = Decimal("0.01")
STATUSES = {
    "present": ("Full Day", Decimal("1")),
    "full day": ("Full Day", Decimal("1")),
    "full": ("Full Day", Decimal("1")),
    "half day": ("Half Day", Decimal("0.5")),
    "half-day": ("Half Day", Decimal("0.5")),
    "half": ("Half Day", Decimal("0.5")),
    "absent": ("Absent", ZERO),
    "unpaid leave": ("Unpaid Leave", ZERO),
    "unpaid": ("Unpaid Leave", ZERO),
}
METHODS = {"Cash", "UPI", "Bank Transfer", "Cheque", "Other"}


def amount(value, *, positive=True, allow_negative=False):
    try:
        if value is None or value == "" or isinstance(value, bool):
            raise ValueError()
        result = Decimal(str(value))
        if not result.is_finite() or abs(result) >= Decimal("10000000000") or (positive and result <= 0) or (not allow_negative and result < 0):
            raise ValueError()
        return result.quantize(CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError({"amount": "Enter a valid monetary amount."})


def parsed_date(value, field="date", default=None):
    if value in (None, "") and default is not None:
        return default
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise ValidationError({field: "Use YYYY-MM-DD."})


def request_key(value):
    value = str(value or "").strip()
    if not value or len(value) > 120:
        raise ValidationError({"requestKey": "A unique submission key is required."})
    return value


def audit(employee, actor, action, *, work_date=None, original=None, updated=None, reason=""):
    WageAuditLog.objects.create(
        company=employee.company, branch=employee.branch, employee=employee,
        actor=actor, action=action, work_date=work_date,
        original=original or {}, updated=updated or {}, reason=reason,
    )


def applied_rate(employee, work_date):
    return EmployeeDailyWageRate.objects.filter(
        company=employee.company, employee=employee,
        effective_from__lte=work_date,
    ).order_by("-effective_from", "-created_at").first()


def sum_amount(qs, key="amount"):
    return qs.aggregate(total=Sum(key))["total"] or ZERO


def totals(employee):
    base = sum_amount(DailyWageEntry.objects.filter(employee=employee), "base_amount")
    extras = sum_amount(DailyWageExtra.objects.filter(employee=employee, status="Approved"))
    adjustments = sum_amount(WageAdjustment.objects.filter(employee=employee))
    paid = sum_amount(WagePayment.objects.filter(
        employee=employee, reversal__isnull=True
    ))
    earned = base + extras + adjustments
    return {
        "totalEarned": earned,
        "totalPaid": paid,
        "currentBalance": earned - paid,
        "baseEarned": base,
        "extraEarned": extras,
        "adjustments": adjustments,
    }


def day_amounts(employee, work_date):
    entry = DailyWageEntry.objects.filter(employee=employee, work_date=work_date).first()
    extras = sum_amount(DailyWageExtra.objects.filter(
        employee=employee, work_date=work_date, status="Approved"
    ))
    adjustments = sum_amount(WageAdjustment.objects.filter(employee=employee, work_date=work_date))
    return {
        "entry": entry, "base": entry.base_amount if entry else ZERO,
        "extras": extras, "adjustments": adjustments,
        "total": (entry.base_amount if entry else ZERO) + extras + adjustments,
    }


@transaction.atomic
def set_rate(employee, user, value, effective_from, reason):
    Employee.objects.select_for_update().get(pk=employee.pk)
    rate = amount(value)
    work_date = parsed_date(effective_from, "effectiveFrom")
    reason = str(reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "Reason is required for every rate change."})
    existing = EmployeeDailyWageRate.objects.filter(employee=employee, effective_from=work_date).first()
    # Snapshot dates already worked must never be silently repriced.
    if existing and DailyWageEntry.objects.filter(
        employee=employee, work_date__gte=work_date
    ).exists():
        raise ValidationError({"effectiveFrom": "Existing effective rate has posted wages. Choose a new effective date."})
    original = {"rate": str(existing.rate)} if existing else {}
    if existing:
        existing.rate, existing.reason, existing.approved_by = rate, reason, user
        existing.save(update_fields=["rate", "reason", "approved_by", "updated_at"])
        result = existing
    else:
        result = EmployeeDailyWageRate.objects.create(
            company=employee.company, branch=employee.branch, employee=employee,
            effective_from=work_date, rate=rate, reason=reason, approved_by=user,
        )
    audit(employee, user, "WAGE_RATE", work_date=work_date,
          original=original, updated={"rate": str(rate)}, reason=reason)
    return result


@transaction.atomic
def finalize_attendance(employee, work_date, status, user, reason):
    Employee.objects.select_for_update().get(pk=employee.pk)
    work_date = parsed_date(work_date)
    if work_date > timezone.localdate():
        raise ValidationError({"date": "Cannot finalize future attendance."})
    rate = applied_rate(employee, work_date)
    if not rate:
        raise ValidationError({"dailyRate": "Configure a daily wage rate effective on this date."})
    current = AttendanceRecord.objects.select_for_update().filter(
        company=employee.company, employee=employee, date=work_date
    ).first()
    requested = str(status or (current.status if current else "")).strip().lower()
    if requested not in STATUSES:
        raise ValidationError({"status": "Use Full Day, Half Day, Absent or Unpaid Leave."})
    normalized, fraction = STATUSES[requested]
    existing = DailyWageEntry.objects.select_for_update().filter(
        employee=employee, work_date=work_date
    ).first()
    # An already-posted day's rate is a historical snapshot: later rate
    # revisions must not silently change that day's agreed pay.
    snapshot_rate = existing.applied_rate if existing else rate.rate
    new_base = (snapshot_rate * fraction).quantize(CENT, rounding=ROUND_HALF_UP)
    if not current:
        current = AttendanceRecord.objects.create(
            company=employee.company, branch=employee.branch, employee=employee,
            date=work_date, status=normalized,
        )
    old_status, old_final = current.status, current.wage_finalized
    current.status = normalized
    current.wage_finalized = True
    current.wage_finalized_by = user
    current.wage_finalized_at = timezone.now()
    current.save(update_fields=[
        "status", "wage_finalized", "wage_finalized_by", "wage_finalized_at", "updated_at",
    ])
    settled = bool(existing) and WagePayment.objects.filter(
        employee=employee, reversal__isnull=True,
        created_at__gte=existing.created_at,
    ).exists()
    old_base = existing.base_amount if existing else ZERO
    if existing and existing.attendance_status != normalized and not str(reason or "").strip():
        raise ValidationError({"reason": "A reason is required to correct finalized attendance."})
    old_rate = existing.applied_rate if existing else None
    if existing and settled:
        # Do not rewrite a historically settled earning. Append the
        # correction delta, even if it reduces the payable balance.
        previous_corrections = sum_amount(WageAdjustment.objects.filter(
            employee=employee, work_date=work_date, kind="AttendanceCorrection",
        ))
        delta = new_base - old_base - previous_corrections
        if delta:
            WageAdjustment.objects.create(
                company=employee.company, branch=employee.branch, employee=employee,
                work_date=work_date, amount=delta, reason=str(reason or "Approved attendance correction"),
                kind="AttendanceCorrection", request_key=str(uuid.uuid4()), created_by=user,
            )
    elif existing:
        existing.attendance_status = normalized
        existing.applied_rate = snapshot_rate
        existing.base_amount = new_base
        existing.finalized_by = user
        existing.save(update_fields=[
            "attendance_status", "applied_rate", "base_amount", "finalized_by", "updated_at"
        ])
    else:
        DailyWageEntry.objects.create(
            company=employee.company, branch=employee.branch, employee=employee,
            attendance=current, work_date=work_date, attendance_status=normalized,
            applied_rate=snapshot_rate, base_amount=new_base, finalized_by=user,
        )
    audit(employee, user, "ATTENDANCE_FINALIZED", work_date=work_date,
          original={"status": old_status, "finalized": old_final,
                    "base": str(old_base), "rate": str(old_rate) if old_rate is not None else None},
          updated={"status": normalized, "base": str(new_base), "rate": str(snapshot_rate)},
          reason=str(reason or "Attendance finalized"))
    return day_amounts(employee, work_date)


def allocate_payment(payment, employee):
    # Allocation only explains what a payment settles. Financial balance is
    # always calculated independently from immutable earnings minus payments.
    ledger = defaultdict(lambda: Decimal("0"))
    for row in DailyWageEntry.objects.filter(employee=employee):
        ledger[row.work_date] += row.base_amount
    for row in DailyWageExtra.objects.filter(employee=employee, status="Approved"):
        ledger[row.work_date] += row.amount
    for row in WageAdjustment.objects.filter(employee=employee):
        ledger[row.work_date] += row.amount
    allocations = defaultdict(lambda: Decimal("0"))
    for row in WagePaymentAllocation.objects.filter(
        employee=employee, payment__reversal__isnull=True
    ).values("work_date").annotate(total=Sum("amount")):
        allocations[row["work_date"]] += row["total"]
    balance_to_allocate = payment.amount
    rows = []
    for day in sorted(ledger):
        due = max(Decimal("0"), ledger[day] - allocations[day])
        take = min(balance_to_allocate, due)
        if take > 0:
            rows.append(WagePaymentAllocation(
                company=employee.company, branch=employee.branch,
                payment=payment, employee=employee,
                work_date=day, amount=take,
            ))
            balance_to_allocate -= take
        if balance_to_allocate <= 0:
            break
    if balance_to_allocate:
        raise ValidationError({"amount": "Unable to allocate the payment to posted daily earnings."})
    WagePaymentAllocation.objects.bulk_create(rows)


@transaction.atomic
def pay_balance(employee, user, value, method, reference, key, payment_date):
    Employee.objects.select_for_update().get(pk=employee.pk)
    key = request_key(key)
    existing = WagePayment.objects.filter(employee=employee, request_key=key).first()
    if existing:
        if WagePaymentReversal.objects.filter(payment=existing).exists():
            raise ValidationError({"requestKey": "This key already belongs to a reversed payment."})
        return existing
    paid_amount = amount(value)
    balance_before = totals(employee)["currentBalance"]
    if paid_amount > balance_before:
        raise ValidationError({"amount": "Payment exceeds current outstanding balance."})
    method = str(method or "").strip()
    if method not in METHODS:
        raise ValidationError({"method": "Select a valid payment method."})
    payment_date = parsed_date(payment_date, "paymentDate", default=timezone.localdate())
    if payment_date > timezone.localdate():
        raise ValidationError({"paymentDate": "Future payment dates are not allowed."})
    payment = WagePayment.objects.create(
        company=employee.company, branch=employee.branch, employee=employee,
        amount=paid_amount, method=method, reference=str(reference or "")[:150],
        request_key=key, payment_date=payment_date, created_by=user,
    )
    allocate_payment(payment, employee)
    audit(employee, user, "WAGE_PAYMENT", work_date=payment_date,
          original={"balance": str(balance_before)},
          updated={"paymentId": str(payment.pk), "amount": str(paid_amount), "method": method,
                   "balance": str(balance_before - paid_amount)},
          reason="Wage payout")
    return payment
