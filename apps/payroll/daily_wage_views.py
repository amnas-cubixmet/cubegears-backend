import uuid
from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Max, Sum
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.accounts.permissions import RolePermission, user_has_permission
from apps.attendance.models import AttendanceRecord
from apps.employees.models import Employee
from .daily_wage_models import (
    DailyWageEntry, DailyWageExtra, EmployeeDailyWageRate,
    WageAdjustment, WageAuditLog, WagePayment, WagePaymentReversal,
)
from .daily_wage_service import (
    amount, applied_rate, audit, day_amounts, finalize_attendance,
    parsed_date, pay_balance, request_key, set_rate, totals, sum_amount,
)


def visible_workers(request):
    qs = Employee.objects.filter(company=request.user.company)
    branch = getattr(request.user, "branch_id", None)
    if branch and not user_has_permission(request.user, "company.manage"):
        qs = qs.filter(branch_id=branch)
    asked_branch = request.query_params.get("branch")
    if asked_branch:
        qs = qs.filter(branch_id=asked_branch)
    return qs.select_related("branch").order_by("name")


def worker_or_404(request, employee_id):
    worker = visible_workers(request).filter(pk=employee_id).first()
    if not worker:
        raise NotFound("Employee not found in your company / permitted branch.")
    return worker


def require_editor(request):
    if not user_has_permission(request.user, "payroll.edit"):
        raise PermissionDenied("Payroll Edit permission required.")


def pack_rate(rate):
    return {
        "id": str(rate.pk), "rate": str(rate.rate),
        "effectiveFrom": str(rate.effective_from), "reason": rate.reason,
    }


def pack_extra(x):
    return {
        "id": str(x.pk), "date": str(x.work_date), "category": x.category,
        "amount": str(x.amount), "reason": x.reason, "status": x.status,
        "createdBy": str(x.created_by_id or ""),
        "reviewedBy": str(x.reviewed_by_id or ""),
        "reviewedAt": x.reviewed_at.isoformat() if x.reviewed_at else None,
    }


def pack_payment(p):
    return {
        "id": str(p.pk), "employeeId": str(p.employee_id),
        "employeeName": p.employee.name, "amount": str(p.amount),
        "date": str(p.payment_date), "method": p.method,
        "reference": p.reference,
        "reversed": hasattr(p, "reversal"),
    }


def history_for(employee=None, request=None):
    entries = DailyWageEntry.objects.select_related("employee", "attendance").filter(
        employee=employee
    ) if employee else DailyWageEntry.objects.select_related("employee", "attendance").filter(
        employee__in=visible_workers(request)
    )
    if request:
        since, until = request.query_params.get("from"), request.query_params.get("to")
        if since:
            entries = entries.filter(work_date__gte=parsed_date(since, "from"))
        if until:
            entries = entries.filter(work_date__lte=parsed_date(until, "to"))
    entries = list(entries.order_by("-work_date", "employee__name")[:600])
    if not entries:
        return []
    ids = [entry.employee_id for entry in entries]
    dates = {entry.work_date for entry in entries}
    extras = DailyWageExtra.objects.filter(
        employee_id__in=ids, work_date__in=dates, status="Approved"
    ).values("employee_id", "work_date").annotate(total=Sum("amount"))
    adjustments = WageAdjustment.objects.filter(
        employee_id__in=ids, work_date__in=dates
    ).values("employee_id", "work_date").annotate(total=Sum("amount"))
    extras_map = {(x["employee_id"], x["work_date"]): x["total"] for x in extras}
    adjustments_map = {(x["employee_id"], x["work_date"]): x["total"] for x in adjustments}
    rows = []
    for entry in entries:
        key = (entry.employee_id, entry.work_date)
        bonus, correction = extras_map.get(key, Decimal("0")), adjustments_map.get(key, Decimal("0"))
        rows.append({
            "id": str(entry.pk), "employeeId": str(entry.employee_id),
            "employeeName": entry.employee.name, "date": str(entry.work_date),
            "attendance": entry.attendance_status, "dailyRate": str(entry.applied_rate),
            "baseWage": str(entry.base_amount), "extras": str(bonus),
            "adjustments": str(correction), "total": str(entry.base_amount + bonus + correction),
            "status": "Posted", "approvedBy": str(entry.finalized_by_id or ""),
        })
    return rows


def account(employee):
    numbers = totals(employee)
    today = timezone.localdate()
    day = day_amounts(employee, today)
    rates = EmployeeDailyWageRate.objects.filter(employee=employee).order_by("-effective_from")
    last = WagePayment.objects.filter(
        employee=employee, reversal__isnull=True
    ).order_by("-payment_date").first()
    attendance = AttendanceRecord.objects.filter(employee=employee, date=today).first()
    return {
        "employee": {
            "id": str(employee.pk), "employeeCode": employee.employee_code,
            "name": employee.name, "branchId": str(employee.branch_id or ""),
            "branchName": employee.branch.name if employee.branch else "No Branch",
            "companyId": str(employee.company_id), "designation": employee.designation,
        },
        "currentRate": str(applied_rate(employee, today).rate) if applied_rate(employee, today) else None,
        "rates": [pack_rate(rate) for rate in rates],
        "today": {
            "date": str(today),
            "attendance": attendance.status if attendance else "Pending",
            "finalized": bool(attendance and attendance.wage_finalized),
            "baseWage": str(day["base"]),
            "extraEarnings": str(day["extras"] + day["adjustments"]),
            "totalWage": str(day["total"]),
        },
        "totalEarned": str(numbers["totalEarned"]),
        "totalPaid": str(numbers["totalPaid"]),
        "currentBalance": str(numbers["currentBalance"]),
        "lastPaymentDate": str(last.payment_date) if last else None,
        "history": history_for(employee=employee)[:120],
        "extras": [pack_extra(x) for x in DailyWageExtra.objects.filter(employee=employee).order_by("-work_date","-created_at")[:120]],
        "payments": [pack_payment(p) for p in WagePayment.objects.select_related("employee").filter(employee=employee).order_by("-created_at")[:120]],
        "audit": [{
            "id": str(log.pk), "date": str(log.work_date) if log.work_date else "",
            "action": log.action, "reason": log.reason,
            "original": log.original, "updated": log.updated,
            "actor": str(log.actor_id or ""), "createdAt": log.created_at.isoformat(),
        } for log in WageAuditLog.objects.filter(employee=employee)[:100]],
    }


class WageAPIView(APIView):
    permission_classes = [RolePermission]
    permission_map = {"GET": "payroll.view", "POST": "payroll.edit"}


class DailyWageDashboardView(WageAPIView):
    def get(self, request):
        workers = list(visible_workers(request))
        today = timezone.localdate()
        ids = [w.pk for w in workers]
        def sums(model, *, field="amount", **where):
            return {
                row["employee_id"]: row["total"] or Decimal("0")
                for row in model.objects.filter(employee_id__in=ids, **where)
                    .values("employee_id").annotate(total=Sum(field))
            }
        base = sums(DailyWageEntry, field="base_amount")
        extras = sums(DailyWageExtra, status="Approved")
        corrections = sums(WageAdjustment)
        paid = sums(WagePayment, reversal__isnull=True)
        todays = {
            entry.employee_id: entry for entry in
            DailyWageEntry.objects.filter(employee_id__in=ids, work_date=today)
        }
        today_extras = sums(DailyWageExtra, status="Approved", work_date=today)
        today_adjustments = sums(WageAdjustment, work_date=today)
        attendance = {
            row.employee_id: row for row in
            AttendanceRecord.objects.filter(employee_id__in=ids, date=today)
        }
        rates = {}
        for r in EmployeeDailyWageRate.objects.filter(
            employee_id__in=ids, effective_from__lte=today
        ).order_by("employee_id", "-effective_from", "-created_at"):
            rates.setdefault(r.employee_id, r)
        latest_payments = {
            row["employee_id"]: row["date"] for row in
            WagePayment.objects.filter(
                employee_id__in=ids, reversal__isnull=True
            ).values("employee_id").annotate(date=Max("payment_date"))
        }
        rows = []
        for worker in workers:
            pk = worker.pk
            record = attendance.get(pk)
            entry = todays.get(pk)
            today_extra = today_extras.get(pk, Decimal("0")) + today_adjustments.get(pk, Decimal("0"))
            today_base = entry.base_amount if entry else Decimal("0")
            earned = base.get(pk, Decimal("0")) + extras.get(pk, Decimal("0")) + corrections.get(pk, Decimal("0"))
            total_paid = paid.get(pk, Decimal("0"))
            rows.append({
                "id": str(pk), "name": worker.name, "employeeCode": worker.employee_code,
                "companyId": str(worker.company_id), "branchId": str(worker.branch_id or ""),
                "branchName": worker.branch.name if worker.branch else "No Branch",
                "designation": worker.designation,
                "dailyRate": str(rates[pk].rate) if pk in rates else None,
                "today": {
                    "date": str(today),
                    "attendance": record.status if record else "Pending",
                    "finalized": bool(record and record.wage_finalized),
                    "baseWage": str(today_base), "extraEarnings": str(today_extra),
                    "totalWage": str(today_base + today_extra),
                },
                "totalEarned": str(earned), "totalPaid": str(total_paid),
                "currentBalance": str(earned - total_paid),
                "lastPaymentDate": str(latest_payments[pk]) if pk in latest_payments else None,
            })
        return Response({
            "employees": rows,
            "totalWorkers": len(rows),
            "todayExpense": str(sum((Decimal(x["today"]["totalWage"]) for x in rows), Decimal("0"))),
            "outstanding": str(sum((Decimal(x["currentBalance"]) for x in rows), Decimal("0"))),
            "totalPayments": str(sum((Decimal(x["totalPaid"]) for x in rows), Decimal("0"))),
            "workersUnpaid": sum(Decimal(x["currentBalance"]) > 0 for x in rows),
            "todayAttendance": {
                "full": sum(x["today"]["finalized"] and x["today"]["attendance"] in {"Present", "Full Day"} for x in rows),
                "half": sum(x["today"]["finalized"] and x["today"]["attendance"] == "Half Day" for x in rows),
                "pending": sum(not x["today"]["finalized"] for x in rows),
            },
        })


class DailyWageAccountView(WageAPIView):
    def get(self, request, employee_id):
        return Response(account(worker_or_404(request, employee_id)))


class DailyWageHistoryView(WageAPIView):
    def get(self, request):
        return Response(history_for(request=request))


class DailyWagePaymentListView(WageAPIView):
    def get(self, request):
        employee_id = request.query_params.get("employee")
        qs = WagePayment.objects.select_related("employee").filter(employee__in=visible_workers(request))
        if employee_id:
            qs = qs.filter(employee_id=employee_id)
        return Response([pack_payment(p) for p in qs.order_by("-created_at")[:400]])


class DailyWageRateView(WageAPIView):
    def post(self, request, employee_id):
        employee = worker_or_404(request, employee_id)
        rate = set_rate(
            employee, request.user, request.data.get("rate"),
            request.data.get("effectiveFrom"), request.data.get("reason"),
        )
        return Response(pack_rate(rate), status=201)


class DailyWageFinalizeView(WageAPIView):
    def post(self, request, employee_id):
        employee = worker_or_404(request, employee_id)
        day = finalize_attendance(
            employee, request.data.get("date"), request.data.get("status"),
            request.user, request.data.get("reason"),
        )
        return Response({
            "date": str(parsed_date(request.data.get("date"))),
            "base": str(day["base"]), "extras": str(day["extras"]),
            "total": str(day["total"]),
            "balance": str(totals(employee)["currentBalance"]),
        })


class DailyWageExtraView(WageAPIView):
    @transaction.atomic
    def post(self, request, employee_id):
        employee = worker_or_404(request, employee_id)
        Employee.objects.select_for_update().get(pk=employee.pk)
        key = request_key(request.data.get("requestKey"))
        existing = DailyWageExtra.objects.filter(employee=employee, request_key=key).first()
        if existing:
            return Response(pack_extra(existing))
        category = str(request.data.get("category") or "").upper()
        if category not in dict(DailyWageExtra.CATEGORIES):
            raise ValidationError({"category": "Invalid extra earning category."})
        work_date = parsed_date(request.data.get("date"))
        if work_date > timezone.localdate():
            raise ValidationError({"date": "Cannot create future earnings."})
        reason = str(request.data.get("reason") or "").strip()
        if not reason:
            raise ValidationError({"reason": "Explain this additional earning."})
        extra = DailyWageExtra.objects.create(
            company=employee.company, branch=employee.branch, employee=employee,
            work_date=work_date, category=category, reason=reason,
            amount=amount(request.data.get("amount")),
            request_key=key, created_by=request.user,
        )
        audit(employee, request.user, "EXTRA_REQUESTED", work_date=work_date,
              updated={"extra": str(extra.pk), "amount": str(extra.amount)}, reason=reason)
        return Response(pack_extra(extra), status=201)


class DailyWageExtraApprovalView(WageAPIView):
    @transaction.atomic
    def post(self, request, extra_id):
        extra = DailyWageExtra.objects.select_related("employee").filter(
            pk=extra_id, employee__in=visible_workers(request)
        ).first()
        if not extra:
            raise NotFound("Extra earning not found.")
        Employee.objects.select_for_update().get(pk=extra.employee_id)
        extra = DailyWageExtra.objects.select_for_update().get(pk=extra.pk)
        desired = str(request.data.get("status") or "").title()
        if desired not in {"Approved", "Rejected"}:
            raise ValidationError({"status": "Use Approved or Rejected."})
        if extra.status != "Pending":
            if extra.status == desired:
                return Response(pack_extra(extra))
            raise ValidationError({"status": "Reviewed extra earnings are immutable."})
        if desired == "Approved" and not AttendanceRecord.objects.filter(
            employee=extra.employee, date=extra.work_date, wage_finalized=True,
        ).exists():
            raise ValidationError({"date": "Finalize this day's attendance before approving extras."})
        extra.status = desired
        extra.reviewed_by = request.user
        extra.reviewed_at = timezone.now()
        extra.save(update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"])
        audit(extra.employee, request.user, "EXTRA_" + desired.upper(),
              work_date=extra.work_date, updated={"extra": str(extra.pk), "amount": str(extra.amount)},
              reason=extra.reason)
        return Response(pack_extra(extra))


class DailyWageAdjustmentView(WageAPIView):
    @transaction.atomic
    def post(self, request, employee_id):
        employee = worker_or_404(request, employee_id)
        Employee.objects.select_for_update().get(pk=employee.pk)
        key = request_key(request.data.get("requestKey"))
        existing = WageAdjustment.objects.filter(employee=employee, request_key=key).first()
        if existing:
            return Response({"id": str(existing.pk), "amount": str(existing.amount)})
        reason = str(request.data.get("reason") or "").strip()
        if not reason:
            raise ValidationError({"reason": "Every manual adjustment requires a reason."})
        raw = amount(request.data.get("amount"), positive=False, allow_negative=True)
        if raw == 0:
            raise ValidationError({"amount": "Adjustment must not be zero."})
        entry = WageAdjustment.objects.create(
            company=employee.company, branch=employee.branch, employee=employee,
            work_date=parsed_date(request.data.get("date")), amount=raw,
            reason=reason, kind="Manual", created_by=request.user, request_key=key,
        )
        audit(employee, request.user, "MANUAL_ADJUSTMENT", work_date=entry.work_date,
              updated={"amount": str(raw), "adjustment": str(entry.pk)}, reason=reason)
        return Response({"id": str(entry.pk), "amount": str(raw), "balance": str(totals(employee)["currentBalance"])}, status=201)


class DailyWagePayView(WageAPIView):
    def post(self, request, employee_id):
        employee = worker_or_404(request, employee_id)
        payment = pay_balance(
            employee, request.user, request.data.get("amount"),
            request.data.get("method"), request.data.get("reference"),
            request.data.get("requestKey"), request.data.get("paymentDate"),
        )
        return Response({
            "payment": pack_payment(payment),
            "balance": str(totals(employee)["currentBalance"]),
        }, status=200)


class DailyWageReversalView(WageAPIView):
    @transaction.atomic
    def post(self, request, payment_id):
        payment = WagePayment.objects.select_related("employee").filter(
            pk=payment_id, employee__in=visible_workers(request)
        ).first()
        if not payment:
            raise NotFound("Payment not found.")
        employee = payment.employee
        Employee.objects.select_for_update().get(pk=employee.pk)
        reason = str(request.data.get("reason") or "").strip()
        if not reason:
            raise ValidationError({"reason": "A reversal reason is required."})
        reversal, created = WagePaymentReversal.objects.get_or_create(
            payment=payment, defaults={
                "company": employee.company, "branch": employee.branch,
                "reason": reason, "approved_by": request.user,
            }
        )
        if created:
            audit(employee, request.user, "PAYMENT_REVERSED", work_date=payment.payment_date,
                  original={"payment": str(payment.pk), "amount": str(payment.amount)},
                  updated={"reversal": str(reversal.pk)}, reason=reason)
        return Response({"payment": pack_payment(payment), "balance": str(totals(employee)["currentBalance"])})
