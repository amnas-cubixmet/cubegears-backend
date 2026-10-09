from decimal import Decimal

from django.db import models
from django.db.models import Q

from common.models import CompanyOwnedModel


class EmployeeDailyWageRate(CompanyOwnedModel):
    employee = models.ForeignKey("employees.Employee", on_delete=models.PROTECT, related_name="daily_wage_rates")
    rate = models.DecimalField(max_digits=12, decimal_places=2)
    effective_from = models.DateField()
    reason = models.CharField(max_length=250)
    approved_by = models.ForeignKey("accounts.User", null=True, on_delete=models.SET_NULL)
    class Meta:
        ordering = ["-effective_from"]
        constraints = [
            models.UniqueConstraint(fields=["employee", "effective_from"], name="unique_employee_daily_rate_date"),
            models.CheckConstraint(condition=Q(rate__gt=0), name="daily_rate_positive"),
        ]


class DailyWageEntry(CompanyOwnedModel):
    employee = models.ForeignKey("employees.Employee", on_delete=models.PROTECT, related_name="daily_wage_entries")
    attendance = models.OneToOneField("attendance.AttendanceRecord", on_delete=models.PROTECT, related_name="daily_wage_entry")
    work_date = models.DateField()
    attendance_status = models.CharField(max_length=40)
    applied_rate = models.DecimalField(max_digits=12, decimal_places=2)
    base_amount = models.DecimalField(max_digits=12, decimal_places=2)
    finalized_by = models.ForeignKey("accounts.User", null=True, on_delete=models.SET_NULL)
    class Meta:
        ordering = ["-work_date"]
        constraints = [models.UniqueConstraint(fields=["employee", "work_date"], name="unique_employee_daily_earning")]


class DailyWageExtra(CompanyOwnedModel):
    CATEGORIES = [
        ("OT", "Overtime"), ("OD", "Extra Duty"), ("BONUS", "Special Work Bonus"),
        ("JOB", "Additional Job Payment"), ("MANUAL", "Manual Extra Earning"),
        ("CUSTOM", "Custom Earning"),
    ]
    employee = models.ForeignKey("employees.Employee", on_delete=models.PROTECT, related_name="daily_wage_extras")
    work_date = models.DateField()
    category = models.CharField(max_length=20, choices=CATEGORIES)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.TextField()
    status = models.CharField(max_length=15, choices=[("Pending", "Pending"), ("Approved", "Approved"), ("Rejected", "Rejected")], default="Pending")
    request_key = models.CharField(max_length=120)
    created_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True, related_name="created_daily_wage_extras")
    reviewed_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True, related_name="reviewed_daily_wage_extras")
    reviewed_at = models.DateTimeField(null=True)
    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["employee", "request_key"], name="unique_employee_wage_extra_key"),
            models.CheckConstraint(condition=Q(amount__gt=0), name="daily_wage_extra_positive"),
        ]


class WageAdjustment(CompanyOwnedModel):
    employee = models.ForeignKey("employees.Employee", on_delete=models.PROTECT, related_name="daily_wage_adjustments")
    work_date = models.DateField()
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.TextField()
    kind = models.CharField(max_length=32, default="Manual")
    created_by = models.ForeignKey("accounts.User", null=True, on_delete=models.SET_NULL)
    request_key = models.CharField(max_length=120)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["employee", "request_key"], name="unique_employee_wage_adjustment_key")]


class WagePayment(CompanyOwnedModel):
    employee = models.ForeignKey("employees.Employee", on_delete=models.PROTECT, related_name="daily_wage_payments")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_date = models.DateField()
    method = models.CharField(max_length=30)
    reference = models.CharField(max_length=150, blank=True)
    request_key = models.CharField(max_length=120)
    created_by = models.ForeignKey("accounts.User", null=True, on_delete=models.SET_NULL)
    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["employee", "request_key"], name="unique_employee_daily_wage_payment"),
            models.CheckConstraint(condition=Q(amount__gt=0), name="daily_wage_payment_positive"),
        ]


class WagePaymentReversal(CompanyOwnedModel):
    payment = models.OneToOneField(WagePayment, on_delete=models.PROTECT, related_name="reversal")
    reason = models.TextField()
    approved_by = models.ForeignKey("accounts.User", null=True, on_delete=models.SET_NULL)


class WageAuditLog(CompanyOwnedModel):
    employee = models.ForeignKey("employees.Employee", on_delete=models.PROTECT, related_name="daily_wage_audit")
    actor = models.ForeignKey("accounts.User", null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=70)
    work_date = models.DateField(null=True)
    original = models.JSONField(default=dict)
    updated = models.JSONField(default=dict)
    reason = models.TextField(blank=True)
    class Meta:
        ordering = ["-created_at"]
