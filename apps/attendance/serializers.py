from rest_framework import serializers
from .models import AttendanceRecord,LeaveRequest,OvertimeRequest,Holiday,AttendanceRule
class AttendanceRecordSerializer(serializers.ModelSerializer):
    employeeName=serializers.CharField(source="employee.name",read_only=True)
    class Meta: model=AttendanceRecord; exclude=("company","branch")
class LeaveRequestSerializer(serializers.ModelSerializer):
    employeeName=serializers.CharField(source="employee.name",read_only=True)
    class Meta: model=LeaveRequest; exclude=("company","branch")
class OvertimeRequestSerializer(serializers.ModelSerializer):
    employeeName=serializers.CharField(source="employee.name",read_only=True)
    class Meta: model=OvertimeRequest; exclude=("company","branch")
class HolidaySerializer(serializers.ModelSerializer):
    class Meta: model=Holiday; exclude=("company","branch")
class AttendanceRuleSerializer(serializers.ModelSerializer):
    class Meta: model=AttendanceRule; exclude=("company","branch")
