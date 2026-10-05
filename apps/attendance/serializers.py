from rest_framework import serializers
from .models import AttendanceRecord,LeaveRequest,OvertimeRequest,Holiday,AttendanceRule,PunchCorrection,LeaveType
class AttendanceRecordSerializer(serializers.ModelSerializer):
    employeeName=serializers.CharField(source="employee.name",read_only=True)
    class Meta: model=AttendanceRecord; exclude=("company","branch")
class LeaveRequestSerializer(serializers.ModelSerializer):
    employeeName=serializers.CharField(source="employee.name",read_only=True)
    class Meta: model=LeaveRequest; exclude=("company","branch")
class OvertimeRequestSerializer(serializers.ModelSerializer):
    staffId=serializers.UUIDField(source="employee_id",read_only=True)
    staffName=serializers.CharField(source="employee.name",read_only=True)
    overtimeHours=serializers.SerializerMethodField()
    payrollMonth=serializers.CharField(source="payroll_month",required=False,allow_blank=True)
    approvedBy=serializers.CharField(source="approved_by.name",read_only=True)
    approvedAt=serializers.DateTimeField(source="approved_at",read_only=True)
    rejectionReason=serializers.CharField(source="rejection_reason",read_only=True)
    auditHistory=serializers.JSONField(source="audit_history",read_only=True)
    class Meta:
        model=OvertimeRequest
        exclude=("company","branch","payroll_month","approved_at","rejection_reason","audit_history")
    def get_overtimeHours(self,obj): return round(obj.minutes/60,2)
class HolidaySerializer(serializers.ModelSerializer):
    class Meta: model=Holiday; exclude=("company","branch")
class AttendanceRuleSerializer(serializers.ModelSerializer):
    class Meta: model=AttendanceRule; exclude=("company","branch")

class PunchCorrectionSerializer(serializers.ModelSerializer):
    staffName=serializers.CharField(source="employee.name",read_only=True)
    class Meta: model=PunchCorrection; exclude=("company","branch")

class LeaveTypeSerializer(serializers.ModelSerializer):
    type=serializers.CharField(source="leave_type",required=False)
    annualAllocation=serializers.DecimalField(source="annual_allocation",max_digits=6,decimal_places=2,required=False)
    halfDay=serializers.BooleanField(source="half_day",required=False)
    maxCarryForward=serializers.DecimalField(source="max_carry_forward",max_digits=6,decimal_places=2,required=False)
    allocation=serializers.SerializerMethodField()
    carryForward=serializers.SerializerMethodField()
    class Meta:
        model=LeaveType
        exclude=("company","branch","leave_type","annual_allocation","half_day","max_carry_forward")
    def get_allocation(self,obj): return f"{obj.annual_allocation} Days / Year"
    def get_carryForward(self,obj): return f"{obj.max_carry_forward} Days"
