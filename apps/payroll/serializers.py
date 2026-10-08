from calendar import month_name

from rest_framework import serializers

from apps.branches.models import Branch
from apps.employees.models import Employee
from apps.jobs.models import Job
from .models import (
    CommissionRule,
    CompensationComponent,
    EmployeeCommission,
    EmployeeCompensationPlan,
    EmployeeWorkLog,
    Incentive,
    JobCardEmployeeAssignment,
    PayrollAdjustment,
    PayrollLineItem,
    PayrollPeriod,
    PayrollPolicy,
    PayrollRun,
    Payslip,
    SalaryAdvance,
    SalaryPayment,
    SalaryStructure,
)


class PayrollPolicySerializer(serializers.ModelSerializer):
    defaultPaymentType=serializers.CharField(source="default_payment_type",required=False)
    payrollCycle=serializers.CharField(source="payroll_cycle",required=False)
    workingDayCalculation=serializers.CharField(source="working_day_calculation",required=False)
    overtimeRules=serializers.JSONField(source="overtime_rules",required=False)
    commissionRules=serializers.JSONField(source="commission_rules",required=False)
    jobIncentiveRules=serializers.JSONField(source="job_incentive_rules",required=False)
    approvalWorkflow=serializers.JSONField(source="approval_workflow",required=False)
    paymentMethods=serializers.JSONField(source="payment_methods",required=False)
    unpaidLeavePolicy=serializers.JSONField(source="unpaid_leave_policy",required=False)
    branchId=serializers.PrimaryKeyRelatedField(
        source="branch",
        queryset=Branch.objects.all(),
        required=False,
        allow_null=True,
    )

    class Meta:
        model=PayrollPolicy
        exclude=(
            "company","branch","default_payment_type","payroll_cycle",
            "working_day_calculation","overtime_rules","commission_rules",
            "job_incentive_rules","approval_workflow","payment_methods",
            "unpaid_leave_policy",
        )

    def validate_branchId(self,value):
        request=self.context.get("request")
        company=getattr(getattr(request,"user",None),"company",None)
        if value and company and value.company_id!=company.id:
            raise serializers.ValidationError("Invalid branch for this company.")
        return value


class CompensationComponentSerializer(serializers.ModelSerializer):
    calculationType=serializers.CharField(source="calculation_type",required=False)
    revenueBasis=serializers.CharField(source="revenue_basis",required=False,allow_blank=True)

    class Meta:
        model=CompensationComponent
        exclude=("company","branch","calculation_type","revenue_basis")


class EmployeeCompensationPlanSerializer(serializers.ModelSerializer):
    staffId=serializers.UUIDField(source="employee_id",read_only=True)
    staffName=serializers.CharField(source="employee.name",read_only=True)
    employee=serializers.PrimaryKeyRelatedField(queryset=Employee.objects.all(),write_only=True,required=False)
    paymentType=serializers.CharField(source="payment_type",required=False)
    baseSalary=serializers.DecimalField(source="base_salary",max_digits=12,decimal_places=2,required=False)
    dailyWageRate=serializers.DecimalField(source="daily_wage_rate",max_digits=12,decimal_places=2,required=False)
    hourlyWageRate=serializers.DecimalField(source="hourly_wage_rate",max_digits=12,decimal_places=2,required=False)
    commissionType=serializers.CharField(source="commission_type",required=False)
    commissionPercentage=serializers.DecimalField(source="commission_percentage",max_digits=7,decimal_places=4,required=False)
    commissionFixedAmount=serializers.DecimalField(source="commission_fixed_amount",max_digits=12,decimal_places=2,required=False)
    eligibleRevenueBasis=serializers.CharField(source="eligible_revenue_basis",required=False)
    overtimeEligibility=serializers.BooleanField(source="overtime_eligible",required=False)
    incentiveEligibility=serializers.BooleanField(source="incentive_eligible",required=False)
    bonusRules=serializers.JSONField(source="bonus_rules",required=False)
    applicableDeductions=serializers.JSONField(source="applicable_deductions",required=False)
    effectiveDate=serializers.DateField(source="effective_from",required=False)
    effectiveTo=serializers.DateField(source="effective_to",required=False,allow_null=True)
    paymentFrequency=serializers.CharField(source="payment_frequency",required=False)
    approvalStatus=serializers.CharField(source="approval_status",required=False)
    isActive=serializers.BooleanField(source="is_active",required=False)
    approvedBy=serializers.CharField(source="approved_by.name",read_only=True)
    approvedAt=serializers.DateTimeField(source="approved_at",read_only=True)
    components=CompensationComponentSerializer(many=True,read_only=True)
    selectedPayStructure=serializers.SerializerMethodField()

    class Meta:
        model=EmployeeCompensationPlan
        exclude=(
            "company","branch","payment_type","base_salary","daily_wage_rate",
            "hourly_wage_rate","commission_type","commission_percentage",
            "commission_fixed_amount","eligible_revenue_basis","overtime_eligible",
            "incentive_eligible","bonus_rules","applicable_deductions",
            "effective_from","effective_to","payment_frequency","approval_status",
            "is_active","approved_at",
        )

    def get_selectedPayStructure(self,obj):
        return obj.get_payment_type_display()

    def validate_employee(self,value):
        request=self.context.get("request")
        company=getattr(getattr(request,"user",None),"company",None)
        if company and value.company_id!=company.id:
            raise serializers.ValidationError("Employee belongs to another company.")
        return value

    def validate(self,attrs):
        payment_type=attrs.get("payment_type",getattr(self.instance,"payment_type",PayrollPolicy.PAYMENT_MONTHLY))
        commission_type=attrs.get("commission_type",getattr(self.instance,"commission_type",EmployeeCompensationPlan.COMMISSION_NONE))
        percentage=attrs.get("commission_percentage",getattr(self.instance,"commission_percentage",0))
        effective_from=attrs.get("effective_from",getattr(self.instance,"effective_from",None))
        effective_to=attrs.get("effective_to",getattr(self.instance,"effective_to",None))

        commission_types={
            PayrollPolicy.PAYMENT_COMMISSION,
            PayrollPolicy.PAYMENT_MONTHLY_COMMISSION,
            PayrollPolicy.PAYMENT_DAILY_COMMISSION,
            PayrollPolicy.PAYMENT_HOURLY_COMMISSION,
        }
        if payment_type in commission_types and commission_type==EmployeeCompensationPlan.COMMISSION_NONE:
            raise serializers.ValidationError({"commissionType":"Commission type is required for this payment type."})
        if commission_type==EmployeeCompensationPlan.COMMISSION_PERCENTAGE and percentage and percentage>100:
            raise serializers.ValidationError({"commissionPercentage":"Commission percentage cannot exceed 100."})
        if effective_from and effective_to and effective_to<effective_from:
            raise serializers.ValidationError({"effectiveTo":"Effective end date cannot be before effective date."})
        return attrs


class CommissionRuleSerializer(serializers.ModelSerializer):
    staffId=serializers.UUIDField(source="employee_id",read_only=True)
    staffName=serializers.CharField(source="employee.name",read_only=True)
    commissionType=serializers.CharField(source="commission_type",required=False)
    revenueBasis=serializers.CharField(source="revenue_basis",required=False)
    fixedAmount=serializers.DecimalField(source="fixed_amount",max_digits=12,decimal_places=2,required=False)
    minimumRevenue=serializers.DecimalField(source="minimum_revenue",max_digits=12,decimal_places=2,required=False)
    allocationPercent=serializers.DecimalField(source="allocation_percent",max_digits=7,decimal_places=4,required=False)
    effectiveDate=serializers.DateField(source="effective_from",required=False)
    effectiveTo=serializers.DateField(source="effective_to",required=False,allow_null=True)

    class Meta:
        model=CommissionRule
        exclude=(
            "company","branch","commission_type","revenue_basis","fixed_amount",
            "minimum_revenue","allocation_percent","effective_from","effective_to",
        )


class JobCardEmployeeAssignmentSerializer(serializers.ModelSerializer):
    job=serializers.PrimaryKeyRelatedField(queryset=Job.objects.all())
    employee=serializers.PrimaryKeyRelatedField(queryset=Employee.objects.all())
    jobNumber=serializers.CharField(source="job.job_number",read_only=True)
    staffId=serializers.UUIDField(source="employee_id",read_only=True)
    staffName=serializers.CharField(source="employee.name",read_only=True)
    approvedWorkMinutes=serializers.IntegerField(source="approved_work_minutes",required=False)
    approvedWorkHours=serializers.SerializerMethodField()
    eligibleLabourRevenue=serializers.DecimalField(source="eligible_labour_revenue",max_digits=12,decimal_places=2,required=False)
    eligibleServiceRevenue=serializers.DecimalField(source="eligible_service_revenue",max_digits=12,decimal_places=2,required=False)
    commissionAllocationPercent=serializers.DecimalField(source="commission_allocation_percent",max_digits=7,decimal_places=4,required=False)
    completedAt=serializers.DateTimeField(source="completed_at",required=False,allow_null=True)

    class Meta:
        model=JobCardEmployeeAssignment
        exclude=(
            "company","branch","approved_work_minutes","eligible_labour_revenue",
            "eligible_service_revenue","commission_allocation_percent","completed_at",
        )

    def get_approvedWorkHours(self,obj):
        return round(obj.approved_work_minutes/60,2)

    def validate(self,attrs):
        request=self.context.get("request")
        company=getattr(getattr(request,"user",None),"company",None)
        job=attrs.get("job",getattr(self.instance,"job",None))
        employee=attrs.get("employee",getattr(self.instance,"employee",None))
        allocation=attrs.get("commission_allocation_percent",getattr(self.instance,"commission_allocation_percent",100))
        if company and job and job.company_id!=company.id:
            raise serializers.ValidationError({"job":"Job belongs to another company."})
        if company and employee and employee.company_id!=company.id:
            raise serializers.ValidationError({"employee":"Employee belongs to another company."})
        if allocation<0 or allocation>100:
            raise serializers.ValidationError({"commissionAllocationPercent":"Allocation must be between 0 and 100."})
        return attrs


class EmployeeWorkLogSerializer(serializers.ModelSerializer):
    staffId=serializers.UUIDField(source="employee_id",read_only=True)
    staffName=serializers.CharField(source="employee.name",read_only=True)
    jobNumber=serializers.CharField(source="job.job_number",read_only=True)
    workDate=serializers.DateField(source="work_date",required=False)
    workHours=serializers.SerializerMethodField()
    labourRevenue=serializers.DecimalField(source="labour_revenue",max_digits=12,decimal_places=2,required=False)
    serviceRevenue=serializers.DecimalField(source="service_revenue",max_digits=12,decimal_places=2,required=False)
    approvedBy=serializers.CharField(source="approved_by.name",read_only=True)
    approvedAt=serializers.DateTimeField(source="approved_at",read_only=True)

    class Meta:
        model=EmployeeWorkLog
        exclude=("company","branch","work_date","labour_revenue","service_revenue","approved_at")

    def get_workHours(self,obj):
        return round(obj.minutes/60,2)


class EmployeeCommissionSerializer(serializers.ModelSerializer):
    staffId=serializers.UUIDField(source="employee_id",read_only=True)
    staffName=serializers.CharField(source="employee.name",read_only=True)
    jobCardId=serializers.CharField(source="job.job_number",read_only=True)
    payrollMonth=serializers.SerializerMethodField()
    revenueBasis=serializers.CharField(source="revenue_basis",read_only=True)
    eligibleBase=serializers.DecimalField(source="eligible_base",max_digits=12,decimal_places=2,read_only=True)
    commissionType=serializers.CharField(source="commission_type",read_only=True)
    fixedAmount=serializers.DecimalField(source="fixed_amount",max_digits=12,decimal_places=2,read_only=True)
    approvedBy=serializers.CharField(source="approved_by.name",read_only=True)
    approvedAt=serializers.DateTimeField(source="approved_at",read_only=True)

    class Meta:
        model=EmployeeCommission
        exclude=(
            "company","branch","payroll_year","payroll_month","revenue_basis",
            "eligible_base","commission_type","fixed_amount","source_key","approved_at",
        )

    def get_payrollMonth(self,obj):
        return f"{month_name[obj.payroll_month]} {obj.payroll_year}"


class SalaryStructureSerializer(serializers.ModelSerializer):
    staffId=serializers.UUIDField(source="employee_id",read_only=True)
    staffName=serializers.CharField(source="employee.name",read_only=True)
    basicSalary=serializers.DecimalField(source="basic",max_digits=12,decimal_places=2,required=False)
    overtimeRate=serializers.DecimalField(source="overtime_rate",max_digits=10,decimal_places=2,required=False)
    effectiveDate=serializers.DateField(source="effective_from",required=False)
    salaryBasis=serializers.SerializerMethodField()
    dailyRate=serializers.SerializerMethodField()
    hourlyRate=serializers.SerializerMethodField()
    fixedIncentives=serializers.SerializerMethodField()

    class Meta:
        model=SalaryStructure
        exclude=("company","branch","basic","overtime_rate","effective_from")

    def get_salaryBasis(self,obj):
        return (obj.incentive_rule or {}).get("salaryBasis","Fixed Monthly")

    def get_dailyRate(self,obj):
        return (obj.incentive_rule or {}).get("dailyRate",0)

    def get_hourlyRate(self,obj):
        return (obj.incentive_rule or {}).get("hourlyRate",0)

    def get_fixedIncentives(self,obj):
        return (obj.incentive_rule or {}).get("fixed",0)


class SalaryAdvanceSerializer(serializers.ModelSerializer):
    staffId=serializers.UUIDField(source="employee_id",read_only=True)
    staffName=serializers.CharField(source="employee.name",read_only=True)
    advanceAmount=serializers.DecimalField(source="amount",max_digits=12,decimal_places=2,required=False)
    recoveredAmount=serializers.DecimalField(source="recovered_amount",max_digits=12,decimal_places=2,read_only=True)
    outstandingBalance=serializers.DecimalField(source="outstanding_balance",max_digits=12,decimal_places=2,read_only=True)

    class Meta:
        model=SalaryAdvance
        exclude=("company","branch","amount","recovered_amount","outstanding_balance")


class PayrollLineItemSerializer(serializers.ModelSerializer):
    sourceType=serializers.CharField(source="source_type",read_only=True)
    sourceId=serializers.CharField(source="source_id",read_only=True)

    class Meta:
        model=PayrollLineItem
        exclude=("company","branch","source_type","source_id","source_key")


class PayrollAdjustmentSerializer(serializers.ModelSerializer):
    adjustmentType=serializers.CharField(source="adjustment_type",required=False)
    approvedBy=serializers.CharField(source="approved_by.name",read_only=True)
    approvedAt=serializers.DateTimeField(source="approved_at",read_only=True)

    class Meta:
        model=PayrollAdjustment
        exclude=("company","branch","adjustment_type","approved_at")


class SalaryPaymentSerializer(serializers.ModelSerializer):
    staffId=serializers.UUIDField(source="employee_id",read_only=True)
    staffName=serializers.CharField(source="employee.name",read_only=True)
    paymentDate=serializers.DateField(source="payment_date",required=False)
    recordedBy=serializers.CharField(source="recorded_by.name",read_only=True)

    class Meta:
        model=SalaryPayment
        exclude=("company","branch","payment_date")


class PayslipSerializer(serializers.ModelSerializer):
    staffId=serializers.UUIDField(source="employee_id",read_only=True)
    staffName=serializers.CharField(source="employee.name",read_only=True)
    month=serializers.SerializerMethodField()
    paymentType=serializers.CharField(source="payment_type",read_only=True)
    salaryBasis=serializers.CharField(source="payment_type",read_only=True)
    attendanceDays=serializers.DecimalField(source="attendance_days",max_digits=6,decimal_places=2,read_only=True)
    payableDays=serializers.DecimalField(source="payable_days",max_digits=6,decimal_places=2,read_only=True)
    payableHours=serializers.DecimalField(source="payable_hours",max_digits=9,decimal_places=2,read_only=True)
    unpaidLeaveDays=serializers.DecimalField(source="unpaid_leave_days",max_digits=6,decimal_places=2,read_only=True)
    overtimeHours=serializers.SerializerMethodField()
    baseSalary=serializers.DecimalField(source="basic",max_digits=12,decimal_places=2,read_only=True)
    fixedIncentives=serializers.DecimalField(source="incentives",max_digits=12,decimal_places=2,read_only=True)
    approvedCommission=serializers.DecimalField(source="commission_amount",max_digits=12,decimal_places=2,read_only=True)
    bonusAmount=serializers.DecimalField(source="bonus_amount",max_digits=12,decimal_places=2,read_only=True)
    overtimePay=serializers.DecimalField(source="overtime_amount",max_digits=12,decimal_places=2,read_only=True)
    grossSalary=serializers.DecimalField(source="gross",max_digits=12,decimal_places=2,read_only=True)
    netSalary=serializers.DecimalField(source="net",max_digits=12,decimal_places=2,read_only=True)
    advanceRecovery=serializers.DecimalField(source="advance_recovery",max_digits=12,decimal_places=2,read_only=True)
    paidAmount=serializers.DecimalField(source="paid_amount",max_digits=12,decimal_places=2,read_only=True)
    paymentStatus=serializers.CharField(source="payment_status",read_only=True)
    approvalStatus=serializers.CharField(source="payroll_run.approval_status",read_only=True)
    payments=SalaryPaymentSerializer(source="salary_payments",many=True,read_only=True)
    lineItems=PayrollLineItemSerializer(source="line_items",many=True,read_only=True)
    adjustments=PayrollAdjustmentSerializer(many=True,read_only=True)
    calculationSnapshot=serializers.JSONField(source="calculation_snapshot",read_only=True)

    class Meta:
        model=Payslip
        exclude=(
            "company","branch","payment_type","attendance_days","payable_days",
            "payable_hours","unpaid_leave_days","overtime_amount",
            "commission_amount","bonus_amount","adjustment_amount","gross","net",
            "advance_recovery","paid_amount","payment_status","payment_history",
            "calculation_snapshot",
        )

    def get_month(self,obj):
        return f"{month_name[obj.payroll_run.month]} {obj.payroll_run.year}"

    def get_overtimeHours(self,obj):
        return round(obj.overtime_minutes/60,2)


class PayrollPeriodSerializer(serializers.ModelSerializer):
    branchId=serializers.UUIDField(source="branch_id",read_only=True)
    period=serializers.SerializerMethodField()

    class Meta:
        model=PayrollPeriod
        exclude=("company","branch")

    def get_period(self,obj):
        return f"{month_name[obj.month]} {obj.year}"


class PayrollRunSerializer(serializers.ModelSerializer):
    payslips=PayslipSerializer(many=True,read_only=True)
    periodLabel=serializers.SerializerMethodField()
    approvalStatus=serializers.CharField(source="approval_status",read_only=True)
    approvedBy=serializers.CharField(source="approved_by.name",read_only=True)
    approvedAt=serializers.DateTimeField(source="approved_at",read_only=True)
    branchId=serializers.PrimaryKeyRelatedField(
        source="branch",
        queryset=Branch.objects.all(),
        required=False,
        allow_null=True,
    )

    class Meta:
        model=PayrollRun
        exclude=("company","branch","approval_status","approved_at")

    def validate_branchId(self,value):
        request=self.context.get("request")
        company=getattr(getattr(request,"user",None),"company",None)
        if value and company and value.company_id!=company.id:
            raise serializers.ValidationError("Invalid branch for this company.")
        return value

    def get_periodLabel(self,obj):
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
