from rest_framework import serializers
from .models import (
    AttendanceRecord,
    AttendanceSession,
    LeaveRequest,
    OvertimeRequest,
    Holiday,
    AttendanceRule,
    PunchCorrection,
    LeaveType,
)


class AttendanceSessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttendanceSession
        exclude = ("company", "branch", "attendance")


class AttendanceRecordSerializer(serializers.ModelSerializer):
    employeeName = serializers.CharField(source="employee.name", read_only=True)
    employeeCode = serializers.CharField(source="employee.employee_code", read_only=True)
    shiftName = serializers.SerializerMethodField()
    sessions = AttendanceSessionSerializer(many=True, read_only=True)

    def get_shiftName(self, obj):
        shift=getattr(obj.employee,"shift",None)
        if shift:
            return f"{shift.name} ({shift.start_time.strftime('%H:%M')} - {shift.end_time.strftime('%H:%M')})"
        return obj.employee.shift_label or "Company Default"

    class Meta:
        model = AttendanceRecord
        exclude = ("company", "branch")


class LeaveRequestSerializer(serializers.ModelSerializer):
    employeeName = serializers.CharField(source="employee.name", read_only=True)

    class Meta:
        model = LeaveRequest
        exclude = ("company", "branch")


class OvertimeRequestSerializer(serializers.ModelSerializer):
    staffId = serializers.UUIDField(source="employee_id", read_only=True)
    employeeCode = serializers.CharField(source="employee.employee_code", read_only=True)
    staffName = serializers.CharField(source="employee.name", read_only=True)
    overtimeHours = serializers.SerializerMethodField()
    payrollMonth = serializers.CharField(source="payroll_month", required=False, allow_blank=True)
    approvedBy = serializers.CharField(source="approved_by.name", read_only=True)
    approvedAt = serializers.DateTimeField(source="approved_at", read_only=True)
    rejectionReason = serializers.CharField(source="rejection_reason", read_only=True)
    auditHistory = serializers.JSONField(source="audit_history", read_only=True)

    class Meta:
        model = OvertimeRequest
        exclude = ("company", "branch", "payroll_month", "approved_at", "rejection_reason", "audit_history")

    def get_overtimeHours(self, obj):
        return round(obj.minutes / 60, 2)


class HolidaySerializer(serializers.ModelSerializer):
    class Meta:
        model = Holiday
        exclude = ("company", "branch")


class AttendanceRuleSerializer(serializers.ModelSerializer):
    startTime = serializers.TimeField(source="shift_start_time", required=False)
    endTime = serializers.TimeField(source="shift_end_time", required=False)
    lateGraceMinutes = serializers.IntegerField(source="grace_minutes", required=False)
    overtimeAfterMinutes = serializers.IntegerField(source="overtime_after_minutes", required=False)
    attendanceMode = serializers.CharField(source="attendance_mode", required=False)
    maxSessionsPerDay = serializers.IntegerField(source="max_sessions_per_day", required=False)
    autoCheckoutGraceMinutes = serializers.IntegerField(source="auto_checkout_grace_minutes", required=False)
    missingPunchPolicy = serializers.CharField(source="missing_punch_policy", required=False)
    locationRequired = serializers.BooleanField(source="location_required", required=False)
    locationTrackingEnabled = serializers.BooleanField(source="location_tracking_enabled", required=False)
    missingPunchReminderEnabled = serializers.BooleanField(source="missing_punch_reminder_enabled", required=False)
    missingPunchReminderMinutes = serializers.IntegerField(source="missing_punch_reminder_minutes", required=False)
    leaveRequestNotifications = serializers.BooleanField(source="leave_request_notifications", required=False)
    overtimeRequestNotifications = serializers.BooleanField(source="overtime_request_notifications", required=False)
    correctionApproval = serializers.BooleanField(source="correction_approval", required=False)
    allowSelfApproval = serializers.BooleanField(source="allow_self_approval", required=False)
    weekendDays = serializers.JSONField(source="weekend_days", required=False)
    alternateSaturdayEnabled = serializers.BooleanField(source="alternate_saturday_enabled", required=False)
    alternateSaturdayPattern = serializers.CharField(source="alternate_saturday_pattern", required=False, allow_blank=True)
    weekendAttendancePolicy = serializers.CharField(source="weekend_attendance_policy", required=False)
    weekendEffectiveFrom = serializers.DateField(source="weekend_effective_from", required=False, allow_null=True)

    def validate(self, attrs):
        mode = attrs.get("attendance_mode", getattr(self.instance, "attendance_mode", AttendanceRule.MODE_SINGLE))
        missing_policy = attrs.get(
            "missing_punch_policy",
            getattr(self.instance, "missing_punch_policy", "request_correction"),
        )
        weekend_policy = attrs.get(
            "weekend_attendance_policy",
            getattr(self.instance, "weekend_attendance_policy", "weekly_off"),
        )

        valid_modes = {choice[0] for choice in AttendanceRule.MODE_CHOICES}
        if mode not in valid_modes:
            raise serializers.ValidationError({"attendanceMode": "Invalid attendance mode."})

        if missing_policy not in {"request_correction", "auto_close", "mark_missing"}:
            raise serializers.ValidationError({"missingPunchPolicy": "Invalid missing punch policy."})

        if weekend_policy not in {"weekly_off", "allow", "allow_overtime"}:
            raise serializers.ValidationError({"weekendAttendancePolicy": "Invalid weekend attendance policy."})

        max_sessions = attrs.get(
            "max_sessions_per_day",
            getattr(self.instance, "max_sessions_per_day", 0),
        )
        if mode != AttendanceRule.MODE_MULTI and max_sessions not in {0, 1}:
            attrs["max_sessions_per_day"] = 0

        location_required = attrs.get(
            "location_required",
            getattr(self.instance, "location_required", False),
        )
        if location_required:
            attrs["location_tracking_enabled"] = True

        return attrs

    class Meta:
        model = AttendanceRule
        exclude = (
            "company",
            "branch",
            "shift_start_time",
            "shift_end_time",
            "grace_minutes",
            "overtime_after_minutes",
            "attendance_mode",
            "max_sessions_per_day",
            "auto_checkout_grace_minutes",
            "missing_punch_policy",
            "location_required",
            "location_tracking_enabled",
            "missing_punch_reminder_enabled",
            "missing_punch_reminder_minutes",
            "leave_request_notifications",
            "overtime_request_notifications",
            "correction_approval",
            "allow_self_approval",
            "weekend_days",
            "alternate_saturday_enabled",
            "alternate_saturday_pattern",
            "weekend_attendance_policy",
            "weekend_effective_from",
        )


class PunchCorrectionSerializer(serializers.ModelSerializer):
    staffName = serializers.CharField(source="employee.name", read_only=True)

    class Meta:
        model = PunchCorrection
        exclude = ("company", "branch")


class LeaveTypeSerializer(serializers.ModelSerializer):
    type = serializers.CharField(source="leave_type", required=False)
    allocationMethod = serializers.CharField(source="allocation_method", required=False)
    annualAllocation = serializers.DecimalField(source="annual_allocation", max_digits=6, decimal_places=2, required=False)
    monthlyAllocation = serializers.DecimalField(source="monthly_allocation", max_digits=6, decimal_places=2, required=False)
    halfDay = serializers.BooleanField(source="half_day", required=False)
    maxCarryForward = serializers.DecimalField(source="max_carry_forward", max_digits=6, decimal_places=2, required=False)
    allocation = serializers.SerializerMethodField()
    carryForward = serializers.SerializerMethodField()

    class Meta:
        model = LeaveType
        exclude = (
            "company",
            "branch",
            "leave_type",
            "allocation_method",
            "annual_allocation",
            "monthly_allocation",
            "half_day",
            "max_carry_forward",
        )

    def validate(self, attrs):
        method = attrs.get(
            "allocation_method",
            getattr(self.instance, "allocation_method", LeaveType.ALLOCATION_ANNUAL),
        )
        if method not in {
            LeaveType.ALLOCATION_ANNUAL,
            LeaveType.ALLOCATION_MONTHLY,
            LeaveType.ALLOCATION_MANUAL,
        }:
            raise serializers.ValidationError({"allocationMethod": "Invalid allocation method."})

        annual = attrs.get(
            "annual_allocation",
            getattr(self.instance, "annual_allocation", 0),
        )
        monthly = attrs.get(
            "monthly_allocation",
            getattr(self.instance, "monthly_allocation", 0),
        )
        if annual < 0 or monthly < 0:
            raise serializers.ValidationError("Leave allocation cannot be negative.")
        return attrs

    def get_allocation(self, obj):
        if obj.allocation_method == LeaveType.ALLOCATION_MONTHLY:
            return f"{obj.monthly_allocation} Days / Month"
        if obj.allocation_method == LeaveType.ALLOCATION_MANUAL:
            return "Manual allocation"
        return f"{obj.annual_allocation} Days / Year"

    def get_carryForward(self, obj):
        return f"{obj.max_carry_forward} Days"
