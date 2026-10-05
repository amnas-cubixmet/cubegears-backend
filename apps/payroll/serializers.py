from rest_framework import serializers
from .models import SalaryStructure,SalaryAdvance,PayrollRun,Payslip
class SalaryStructureSerializer(serializers.ModelSerializer):
    employeeName=serializers.CharField(source="employee.name",read_only=True)
    class Meta: model=SalaryStructure; exclude=("company","branch")
class SalaryAdvanceSerializer(serializers.ModelSerializer):
    employeeName=serializers.CharField(source="employee.name",read_only=True)
    class Meta: model=SalaryAdvance; exclude=("company","branch")
class PayslipSerializer(serializers.ModelSerializer):
    employeeName=serializers.CharField(source="employee.name",read_only=True)
    class Meta: model=Payslip; exclude=("company","branch")
class PayrollRunSerializer(serializers.ModelSerializer):
    payslips=PayslipSerializer(many=True,read_only=True)
    class Meta: model=PayrollRun; exclude=("company","branch")
