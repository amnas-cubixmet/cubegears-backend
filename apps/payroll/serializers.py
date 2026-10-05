from calendar import month_name
from rest_framework import serializers
from .models import SalaryStructure,SalaryAdvance,PayrollRun,Payslip,Incentive

class SalaryStructureSerializer(serializers.ModelSerializer):
    staffId=serializers.UUIDField(source="employee_id",read_only=True)
    staffName=serializers.CharField(source="employee.name",read_only=True)
    basicSalary=serializers.DecimalField(source="basic",max_digits=12,decimal_places=2,required=False)
    overtimeRate=serializers.DecimalField(source="overtime_rate",max_digits=10,decimal_places=2,required=False)
    effectiveDate=serializers.DateField(source="effective_from",required=False)
    class Meta:
        model=SalaryStructure
        exclude=("company","branch","basic","overtime_rate","effective_from")

class SalaryAdvanceSerializer(serializers.ModelSerializer):
    staffId=serializers.UUIDField(source="employee_id",read_only=True)
    staffName=serializers.CharField(source="employee.name",read_only=True)
    advanceAmount=serializers.DecimalField(source="amount",max_digits=12,decimal_places=2,required=False)
    recoveredAmount=serializers.DecimalField(source="recovered_amount",max_digits=12,decimal_places=2,read_only=True)
    outstandingBalance=serializers.DecimalField(source="outstanding_balance",max_digits=12,decimal_places=2,read_only=True)
    class Meta:
        model=SalaryAdvance
        exclude=("company","branch","amount","recovered_amount","outstanding_balance")

class PayslipSerializer(serializers.ModelSerializer):
    staffId=serializers.UUIDField(source="employee_id",read_only=True)
    staffName=serializers.CharField(source="employee.name",read_only=True)
    month=serializers.SerializerMethodField()
    overtimeHours=serializers.SerializerMethodField()
    overtimePay=serializers.DecimalField(source="overtime_amount",max_digits=12,decimal_places=2,read_only=True)
    grossSalary=serializers.DecimalField(source="gross",max_digits=12,decimal_places=2,read_only=True)
    netSalary=serializers.DecimalField(source="net",max_digits=12,decimal_places=2,read_only=True)
    advanceRecovery=serializers.DecimalField(source="advance_recovery",max_digits=12,decimal_places=2,read_only=True)
    paidAmount=serializers.DecimalField(source="paid_amount",max_digits=12,decimal_places=2,read_only=True)
    paymentStatus=serializers.CharField(source="payment_status",read_only=True)
    payments=serializers.JSONField(source="payment_history",read_only=True)

    class Meta:
        model=Payslip
        exclude=("company","branch","overtime_amount","gross","net","advance_recovery","paid_amount","payment_status","payment_history")

    def get_month(self,obj):
        return f"{month_name[obj.payroll_run.month]} {obj.payroll_run.year}"

    def get_overtimeHours(self,obj):
        return f"{round(obj.overtime_minutes/60,2)}h"

class PayrollRunSerializer(serializers.ModelSerializer):
    payslips=PayslipSerializer(many=True,read_only=True)
    period=serializers.SerializerMethodField()
    class Meta:
        model=PayrollRun
        exclude=("company","branch")
    def get_period(self,obj):
        return f"{month_name[obj.month]} {obj.year}"

class IncentiveSerializer(serializers.ModelSerializer):
    staffId=serializers.UUIDField(source="employee_id",read_only=True)
    staffName=serializers.CharField(source="employee.name",read_only=True)
    payrollMonth=serializers.CharField(source="payroll_month",required=False,allow_blank=True)
    completionDate=serializers.DateField(source="completion_date",required=False)
    approvedBy=serializers.CharField(source="approved_by.name",read_only=True)
    approvedAt=serializers.DateTimeField(source="approved_at",read_only=True)
    class Meta:
        model=Incentive
        exclude=("company","branch","payroll_month","completion_date","approved_at")
