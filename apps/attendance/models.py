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
    location=models.JSONField(default=dict,blank=True)
    notes=models.TextField(blank=True)
    class Meta:
        ordering=["-date"]
        constraints=[models.UniqueConstraint(fields=["employee","date"],name="unique_employee_attendance_day")]

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
    name=models.CharField(max_length=120,default="Default")
    grace_minutes=models.PositiveIntegerField(default=15)
    overtime_after_minutes=models.PositiveIntegerField(default=540)
    location_required=models.BooleanField(default=False)
    correction_approval=models.BooleanField(default=True)
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
    name=models.CharField(max_length=100)
    code=models.CharField(max_length=30)
    leave_type=models.CharField(max_length=30,default="Paid")
    annual_allocation=models.DecimalField(max_digits=6,decimal_places=2,default=12)
    half_day=models.BooleanField(default=True)
    max_carry_forward=models.DecimalField(max_digits=6,decimal_places=2,default=0)
    status=models.CharField(max_length=20,default="Active")
    class Meta:
        constraints=[models.UniqueConstraint(fields=["company","code"],name="unique_leave_type_code_per_company")]
