from datetime import time
from decimal import Decimal
from django.db import models
from common.models import CompanyOwnedModel

class AttendanceRecord(CompanyOwnedModel):
    employee=models.ForeignKey("employees.Employee",on_delete=models.CASCADE,related_name="attendance_records")
    date=models.DateField()
    clock_in=models.DateTimeField(null=True,blank=True)
    clock_out=models.DateTimeField(null=True,blank=True)
    worked_minutes=models.PositiveIntegerField(default=0)
    late_minutes=models.PositiveIntegerField(default=0)
    early_exit_minutes=models.PositiveIntegerField(default=0)
    overtime_minutes=models.PositiveIntegerField(default=0)
    status=models.CharField(max_length=30,default="Present")
    wage_finalized=models.BooleanField(default=False)
    wage_finalized_by=models.ForeignKey("accounts.User",on_delete=models.SET_NULL,null=True,blank=True,related_name="approved_daily_wage_attendance")
    wage_finalized_at=models.DateTimeField(null=True,blank=True)
    location=models.JSONField(default=dict,blank=True)
    notes=models.TextField(blank=True)
    class Meta:
        ordering=["-date"]
        constraints=[models.UniqueConstraint(fields=["employee","date"],name="unique_employee_attendance_day")]


class AttendanceSession(CompanyOwnedModel):
    attendance=models.ForeignKey(AttendanceRecord,on_delete=models.CASCADE,related_name="sessions")
    session_number=models.PositiveIntegerField(default=1)
    clock_in=models.DateTimeField()
    clock_out=models.DateTimeField(null=True,blank=True)
    worked_minutes=models.PositiveIntegerField(default=0)
    auto_closed=models.BooleanField(default=False)
    source=models.CharField(max_length=30,default="web")
    clock_in_location=models.JSONField(default=dict,blank=True)
    clock_out_location=models.JSONField(default=dict,blank=True)
    note=models.CharField(max_length=255,blank=True)

    class Meta:
        ordering=["session_number"]
        indexes=[
            models.Index(fields=["clock_out"],name="att_session_open_idx"),
        ]
        constraints=[
            models.UniqueConstraint(fields=["attendance","session_number"],name="unique_attendance_session_number"),
            models.UniqueConstraint(
                fields=["attendance"],
                condition=models.Q(clock_out__isnull=True),
                name="unique_open_attendance_session",
            ),
        ]

    @property
    def is_open(self):
        return self.clock_out is None

class LeaveRequest(CompanyOwnedModel):
    employee=models.ForeignKey("employees.Employee",on_delete=models.CASCADE,related_name="leave_requests")
    leave_type=models.CharField(max_length=50)
    start_date=models.DateField()
    end_date=models.DateField()
    half_day=models.BooleanField(default=False)
    reason=models.TextField(blank=True)
    attachment=models.URLField(blank=True)
    status=models.CharField(max_length=30,default="Pending")
    reviewed_by=models.ForeignKey("accounts.User",on_delete=models.SET_NULL,null=True,blank=True,related_name="reviewed_leave_requests")
    reviewed_at=models.DateTimeField(null=True,blank=True)
    manager_note=models.TextField(blank=True,default="")

class OvertimeRequest(CompanyOwnedModel):
    employee=models.ForeignKey("employees.Employee",on_delete=models.CASCADE,related_name="overtime_requests")
    date=models.DateField()
    minutes=models.PositiveIntegerField(default=0)
    reason=models.TextField(blank=True)
    payroll_month=models.CharField(max_length=30,blank=True)
    rate=models.DecimalField(max_digits=10,decimal_places=2,default=0)
    amount=models.DecimalField(max_digits=12,decimal_places=2,default=0)
    status=models.CharField(max_length=30,default="Pending")
    approved_by=models.ForeignKey("accounts.User",on_delete=models.SET_NULL,null=True,blank=True)
    approved_at=models.DateTimeField(null=True,blank=True)
    rejection_reason=models.TextField(blank=True)
    audit_history=models.JSONField(default=list,blank=True)

class Holiday(CompanyOwnedModel):
    date=models.DateField()
    name=models.CharField(max_length=160)
    holiday_type=models.CharField(max_length=50,default="Company")
    is_optional=models.BooleanField(default=False)

class AttendanceRule(CompanyOwnedModel):
    MODE_SINGLE="single"
    MODE_MULTI="multi"
    MODE_AUTO_CHECKOUT="auto_checkout"
    MODE_HYBRID="hybrid"
    MODE_CHOICES=[
        (MODE_SINGLE,"Single check-in / check-out"),
        (MODE_MULTI,"Multiple check-in / check-out sessions"),
        (MODE_AUTO_CHECKOUT,"Check-in with automatic checkout"),
        (MODE_HYBRID,"Manual checkout with automatic fallback"),
    ]

    name=models.CharField(max_length=120,default="Default")
    attendance_mode=models.CharField(max_length=30,choices=MODE_CHOICES,default=MODE_SINGLE)
    shift_start_time=models.TimeField(default=time(9,0))
    shift_end_time=models.TimeField(default=time(18,0))
    grace_minutes=models.PositiveIntegerField(default=15)
    overtime_after_minutes=models.PositiveIntegerField(default=540)
    max_sessions_per_day=models.PositiveIntegerField(default=0)
    auto_checkout_grace_minutes=models.PositiveIntegerField(default=0)
    missing_punch_policy=models.CharField(max_length=30,default="request_correction")
    location_required=models.BooleanField(default=False)
    location_tracking_enabled=models.BooleanField(default=False)
    missing_punch_reminder_enabled=models.BooleanField(default=False)
    missing_punch_reminder_minutes=models.PositiveIntegerField(default=15)
    leave_request_notifications=models.BooleanField(default=True)
    overtime_request_notifications=models.BooleanField(default=True)
    correction_approval=models.BooleanField(default=True)
    allow_self_approval=models.BooleanField(default=False)
    weekend_days=models.JSONField(default=list,blank=True)
    alternate_saturday_enabled=models.BooleanField(default=False)
    alternate_saturday_pattern=models.CharField(max_length=60,blank=True,default="")
    weekend_attendance_policy=models.CharField(max_length=40,default="weekly_off")
    weekend_effective_from=models.DateField(null=True,blank=True)
    is_default=models.BooleanField(default=True)

class PunchCorrection(CompanyOwnedModel):
    attendance=models.ForeignKey(AttendanceRecord,on_delete=models.CASCADE,related_name="corrections")
    employee=models.ForeignKey("employees.Employee",on_delete=models.CASCADE,related_name="punch_corrections")
    original_clock_out=models.DateTimeField(null=True,blank=True)
    proposed_clock_out=models.DateTimeField(null=True,blank=True)
    reason=models.TextField()
    status=models.CharField(max_length=30,default="Pending")
    manager_note=models.TextField(blank=True)
    reviewed_by=models.ForeignKey("accounts.User",on_delete=models.SET_NULL,null=True,blank=True,related_name="reviewed_punch_corrections")
    reviewed_at=models.DateTimeField(null=True,blank=True)

class LeaveType(CompanyOwnedModel):
    ALLOCATION_ANNUAL="annual"
    ALLOCATION_MONTHLY="monthly"
    ALLOCATION_MANUAL="manual"
    ALLOCATION_CHOICES=[
        (ALLOCATION_ANNUAL,"Annual"),
        (ALLOCATION_MONTHLY,"Monthly"),
        (ALLOCATION_MANUAL,"Manual"),
    ]

    name=models.CharField(max_length=100)
    code=models.CharField(max_length=30)
    leave_type=models.CharField(max_length=30,default="Paid")
    allocation_method=models.CharField(max_length=20,choices=ALLOCATION_CHOICES,default=ALLOCATION_ANNUAL)
    annual_allocation=models.DecimalField(max_digits=6,decimal_places=2,default=12)
    monthly_allocation=models.DecimalField(max_digits=6,decimal_places=2,default=0)
    half_day=models.BooleanField(default=True)
    max_carry_forward=models.DecimalField(max_digits=6,decimal_places=2,default=0)
    status=models.CharField(max_length=20,default="Active")
    class Meta:
        constraints=[models.UniqueConstraint(fields=["company","code"],name="unique_leave_type_code_per_company")]
