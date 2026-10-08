from decimal import Decimal

from django.db import models
from django.db.models import Q

from common.models import CompanyOwnedModel


class PayrollPolicy(CompanyOwnedModel):
    PAYMENT_MONTHLY="monthly"
    PAYMENT_DAILY="daily"
    PAYMENT_HOURLY="hourly"
    PAYMENT_COMMISSION="commission"
    PAYMENT_MONTHLY_COMMISSION="monthly_commission"
    PAYMENT_DAILY_COMMISSION="daily_commission"
    PAYMENT_HOURLY_COMMISSION="hourly_commission"
    PAYMENT_SALARY_INCENTIVE="salary_incentive"
    PAYMENT_HYBRID="hybrid"

    PAYMENT_TYPE_CHOICES=[
        (PAYMENT_MONTHLY,"Fixed Monthly Salary"),
        (PAYMENT_DAILY,"Daily Wage"),
        (PAYMENT_HOURLY,"Hourly Wage"),
        (PAYMENT_COMMISSION,"Commission Only"),
        (PAYMENT_MONTHLY_COMMISSION,"Monthly Salary + Commission"),
        (PAYMENT_DAILY_COMMISSION,"Daily Wage + Commission"),
        (PAYMENT_HOURLY_COMMISSION,"Hourly Wage + Commission"),
        (PAYMENT_SALARY_INCENTIVE,"Fixed Salary + Job Incentive"),
        (PAYMENT_HYBRID,"Custom Hybrid Compensation"),
    ]

    name=models.CharField(max_length=120,default="Default")
    default_payment_type=models.CharField(max_length=40,choices=PAYMENT_TYPE_CHOICES,default=PAYMENT_MONTHLY)
    payroll_cycle=models.CharField(max_length=30,default="monthly")
    working_day_calculation=models.CharField(max_length=40,default="attendance")
    overtime_rules=models.JSONField(default=dict,blank=True)
    commission_rules=models.JSONField(default=dict,blank=True)
    job_incentive_rules=models.JSONField(default=dict,blank=True)
    approval_workflow=models.JSONField(default=dict,blank=True)
    payment_methods=models.JSONField(default=list,blank=True)
    unpaid_leave_policy=models.JSONField(default=dict,blank=True)
    is_default=models.BooleanField(default=True)

    class Meta:
        ordering=["name"]
        constraints=[
            models.UniqueConstraint(
                fields=["company","name"],
                condition=Q(branch__isnull=True),
                name="unique_company_payroll_policy",
            ),
            models.UniqueConstraint(
                fields=["company","branch","name"],
                condition=Q(branch__isnull=False),
                name="unique_branch_payroll_policy",
            ),
        ]


class EmployeeCompensationPlan(CompanyOwnedModel):
    COMMISSION_PERCENTAGE="percentage"
    COMMISSION_FIXED="fixed"
    COMMISSION_NONE="none"
    COMMISSION_TYPE_CHOICES=[
        (COMMISSION_NONE,"None"),
        (COMMISSION_PERCENTAGE,"Percentage"),
        (COMMISSION_FIXED,"Fixed Amount"),
    ]

    REVENUE_LABOUR="labour_revenue"
    REVENUE_SERVICE="service_revenue"
    REVENUE_JOB_CARD="job_card"
    REVENUE_CUSTOM="custom"
    REVENUE_CHOICES=[
        (REVENUE_LABOUR,"Labour Revenue"),
        (REVENUE_SERVICE,"Service Revenue"),
        (REVENUE_JOB_CARD,"Job Card Commission"),
        (REVENUE_CUSTOM,"Custom Eligible Revenue"),
    ]

    employee=models.ForeignKey("employees.Employee",on_delete=models.CASCADE,related_name="compensation_plans")
    payment_type=models.CharField(max_length=40,choices=PayrollPolicy.PAYMENT_TYPE_CHOICES,default=PayrollPolicy.PAYMENT_MONTHLY)
    base_salary=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    daily_wage_rate=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    hourly_wage_rate=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    commission_type=models.CharField(max_length=20,choices=COMMISSION_TYPE_CHOICES,default=COMMISSION_NONE)
    commission_percentage=models.DecimalField(max_digits=7,decimal_places=4,default=Decimal("0"))
    commission_fixed_amount=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    eligible_revenue_basis=models.CharField(max_length=30,choices=REVENUE_CHOICES,default=REVENUE_LABOUR)
    overtime_eligible=models.BooleanField(default=True)
    incentive_eligible=models.BooleanField(default=True)
    bonus_rules=models.JSONField(default=dict,blank=True)
    applicable_deductions=models.JSONField(default=list,blank=True)
    effective_from=models.DateField()
    effective_to=models.DateField(null=True,blank=True)
    payment_frequency=models.CharField(max_length=30,default="monthly")
    approval_status=models.CharField(max_length=30,default="Approved")
    is_active=models.BooleanField(default=True)
    notes=models.TextField(blank=True)
    approved_by=models.ForeignKey("accounts.User",on_delete=models.SET_NULL,null=True,blank=True,related_name="approved_compensation_plans")
    approved_at=models.DateTimeField(null=True,blank=True)

    class Meta:
        ordering=["employee","-effective_from","-created_at"]
        constraints=[
            models.UniqueConstraint(
                fields=["employee","effective_from"],
                name="unique_employee_compensation_effective_date",
            ),
        ]


class CompensationComponent(CompanyOwnedModel):
    KIND_EARNING="earning"
    KIND_DEDUCTION="deduction"
    KIND_CHOICES=[(KIND_EARNING,"Earning"),(KIND_DEDUCTION,"Deduction")]

    CALC_FIXED="fixed"
    CALC_PERCENT="percent"
    CALC_PER_DAY="per_day"
    CALC_PER_HOUR="per_hour"
    CALC_CHOICES=[
        (CALC_FIXED,"Fixed"),
        (CALC_PERCENT,"Percent"),
        (CALC_PER_DAY,"Per Day"),
        (CALC_PER_HOUR,"Per Hour"),
    ]

    plan=models.ForeignKey(EmployeeCompensationPlan,on_delete=models.CASCADE,related_name="components")
    code=models.CharField(max_length=60)
    name=models.CharField(max_length=120)
    kind=models.CharField(max_length=20,choices=KIND_CHOICES,default=KIND_EARNING)
    calculation_type=models.CharField(max_length=20,choices=CALC_CHOICES,default=CALC_FIXED)
    amount=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    percentage=models.DecimalField(max_digits=7,decimal_places=4,default=Decimal("0"))
    revenue_basis=models.CharField(max_length=30,blank=True)
    taxable=models.BooleanField(default=True)
    is_active=models.BooleanField(default=True)
    metadata=models.JSONField(default=dict,blank=True)

    class Meta:
        ordering=["kind","name"]
        constraints=[
            models.UniqueConstraint(fields=["plan","code"],name="unique_compensation_component_code"),
        ]


class CommissionRule(CompanyOwnedModel):
    employee=models.ForeignKey("employees.Employee",on_delete=models.CASCADE,related_name="commission_rules")
    plan=models.ForeignKey(EmployeeCompensationPlan,on_delete=models.CASCADE,related_name="commission_rules",null=True,blank=True)
    name=models.CharField(max_length=120,default="Default Commission")
    commission_type=models.CharField(max_length=20,choices=EmployeeCompensationPlan.COMMISSION_TYPE_CHOICES,default=EmployeeCompensationPlan.COMMISSION_PERCENTAGE)
    revenue_basis=models.CharField(max_length=30,choices=EmployeeCompensationPlan.REVENUE_CHOICES,default=EmployeeCompensationPlan.REVENUE_LABOUR)
    percentage=models.DecimalField(max_digits=7,decimal_places=4,default=Decimal("0"))
    fixed_amount=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    minimum_revenue=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    allocation_percent=models.DecimalField(max_digits=7,decimal_places=4,default=Decimal("100"))
    effective_from=models.DateField()
    effective_to=models.DateField(null=True,blank=True)
    is_active=models.BooleanField(default=True)

    class Meta:
        ordering=["employee","-effective_from"]


class JobCardEmployeeAssignment(CompanyOwnedModel):
    job=models.ForeignKey("jobs.Job",on_delete=models.CASCADE,related_name="employee_assignments")
    employee=models.ForeignKey("employees.Employee",on_delete=models.PROTECT,related_name="job_assignments")
    role=models.CharField(max_length=80,blank=True)
    assigned_at=models.DateTimeField(auto_now_add=True)
    completed_at=models.DateTimeField(null=True,blank=True)
    approved_work_minutes=models.PositiveIntegerField(default=0)
    eligible_labour_revenue=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    eligible_service_revenue=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    commission_allocation_percent=models.DecimalField(max_digits=7,decimal_places=4,default=Decimal("100"))
    status=models.CharField(max_length=30,default="Assigned")
    metadata=models.JSONField(default=dict,blank=True)

    class Meta:
        ordering=["-assigned_at"]
        constraints=[
            models.UniqueConstraint(fields=["job","employee"],name="unique_job_employee_assignment"),
        ]


class EmployeeWorkLog(CompanyOwnedModel):
    assignment=models.ForeignKey(JobCardEmployeeAssignment,on_delete=models.CASCADE,related_name="work_logs")
    employee=models.ForeignKey("employees.Employee",on_delete=models.PROTECT,related_name="work_logs")
    job=models.ForeignKey("jobs.Job",on_delete=models.CASCADE,related_name="employee_work_logs")
    work_date=models.DateField()
    minutes=models.PositiveIntegerField(default=0)
    labour_revenue=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    service_revenue=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    status=models.CharField(max_length=30,default="Pending")
    approved_by=models.ForeignKey("accounts.User",on_delete=models.SET_NULL,null=True,blank=True,related_name="approved_employee_work_logs")
    approved_at=models.DateTimeField(null=True,blank=True)
    notes=models.TextField(blank=True)

    class Meta:
        ordering=["-work_date","-created_at"]


class EmployeeCommission(CompanyOwnedModel):
    employee=models.ForeignKey("employees.Employee",on_delete=models.PROTECT,related_name="generated_commissions")
    plan=models.ForeignKey(EmployeeCompensationPlan,on_delete=models.SET_NULL,null=True,blank=True,related_name="generated_commissions")
    assignment=models.ForeignKey(JobCardEmployeeAssignment,on_delete=models.SET_NULL,null=True,blank=True,related_name="commissions")
    job=models.ForeignKey("jobs.Job",on_delete=models.SET_NULL,null=True,blank=True,related_name="employee_commissions")
    payroll_year=models.PositiveSmallIntegerField()
    payroll_month=models.PositiveSmallIntegerField()
    revenue_basis=models.CharField(max_length=30,default=EmployeeCompensationPlan.REVENUE_LABOUR)
    eligible_base=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    commission_type=models.CharField(max_length=20,default=EmployeeCompensationPlan.COMMISSION_PERCENTAGE)
    rate=models.DecimalField(max_digits=7,decimal_places=4,default=Decimal("0"))
    fixed_amount=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    amount=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    source_key=models.CharField(max_length=180)
    status=models.CharField(max_length=30,default="Pending")
    approved_by=models.ForeignKey("accounts.User",on_delete=models.SET_NULL,null=True,blank=True,related_name="approved_employee_commissions")
    approved_at=models.DateTimeField(null=True,blank=True)
    metadata=models.JSONField(default=dict,blank=True)

    class Meta:
        ordering=["-payroll_year","-payroll_month","employee"]
        constraints=[
            models.UniqueConstraint(fields=["company","source_key"],name="unique_employee_commission_source"),
        ]


class SalaryStructure(CompanyOwnedModel):
    """Legacy salary structure retained for compatibility with older clients."""
    employee=models.OneToOneField("employees.Employee",on_delete=models.CASCADE,related_name="salary_structure")
    basic=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    hra=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    allowances=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    deductions=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    overtime_rate=models.DecimalField(max_digits=10,decimal_places=2,default=Decimal("0"))
    incentive_rule=models.JSONField(default=dict,blank=True)
    effective_from=models.DateField()


class SalaryAdvance(CompanyOwnedModel):
    employee=models.ForeignKey("employees.Employee",on_delete=models.CASCADE,related_name="salary_advances")
    date=models.DateField()
    amount=models.DecimalField(max_digits=12,decimal_places=2)
    recovered_amount=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    outstanding_balance=models.DecimalField(max_digits=12,decimal_places=2)
    status=models.CharField(max_length=30,default="Active")
    remarks=models.TextField(blank=True)


class PayrollPeriod(CompanyOwnedModel):
    month=models.PositiveSmallIntegerField()
    year=models.PositiveSmallIntegerField()
    status=models.CharField(max_length=30,default="Open")
    starts_on=models.DateField()
    ends_on=models.DateField()
    locked_at=models.DateTimeField(null=True,blank=True)
    locked_by=models.ForeignKey("accounts.User",on_delete=models.SET_NULL,null=True,blank=True,related_name="locked_payroll_periods")

    class Meta:
        ordering=["-year","-month"]
        constraints=[
            models.UniqueConstraint(
                fields=["company","month","year"],
                condition=Q(branch__isnull=True),
                name="unique_company_payroll_period",
            ),
            models.UniqueConstraint(
                fields=["company","branch","month","year"],
                condition=Q(branch__isnull=False),
                name="unique_branch_payroll_period",
            ),
        ]


class PayrollRun(CompanyOwnedModel):
    month=models.PositiveSmallIntegerField()
    year=models.PositiveSmallIntegerField()
    period=models.ForeignKey(PayrollPeriod,on_delete=models.PROTECT,null=True,blank=True,related_name="runs")
    status=models.CharField(max_length=30,default="Draft")
    approval_status=models.CharField(max_length=30,default="Draft")
    processed_at=models.DateTimeField(null=True,blank=True)
    processed_by=models.ForeignKey("accounts.User",on_delete=models.SET_NULL,null=True,blank=True,related_name="processed_payroll_runs")
    approved_at=models.DateTimeField(null=True,blank=True)
    approved_by=models.ForeignKey("accounts.User",on_delete=models.SET_NULL,null=True,blank=True,related_name="approved_payroll_runs")
    locked_at=models.DateTimeField(null=True,blank=True)
    calculation_version=models.CharField(max_length=30,default="flex-v1")
    metadata=models.JSONField(default=dict,blank=True)

    class Meta:
        ordering=["-year","-month"]
        constraints=[
            models.UniqueConstraint(
                fields=["company","month","year"],
                condition=Q(branch__isnull=True),
                name="unique_payroll_run_company_period",
            ),
            models.UniqueConstraint(
                fields=["company","branch","month","year"],
                condition=Q(branch__isnull=False),
                name="unique_payroll_run_branch_period",
            ),
        ]


class Payslip(CompanyOwnedModel):
    payroll_run=models.ForeignKey(PayrollRun,on_delete=models.CASCADE,related_name="payslips")
    employee=models.ForeignKey("employees.Employee",on_delete=models.PROTECT,related_name="payslips")
    compensation_plan=models.ForeignKey(EmployeeCompensationPlan,on_delete=models.PROTECT,null=True,blank=True,related_name="payslips")
    payment_type=models.CharField(max_length=40,blank=True)
    attendance_days=models.DecimalField(max_digits=6,decimal_places=2,default=Decimal("0"))
    payable_days=models.DecimalField(max_digits=6,decimal_places=2,default=Decimal("0"))
    payable_hours=models.DecimalField(max_digits=9,decimal_places=2,default=Decimal("0"))
    unpaid_leave_days=models.DecimalField(max_digits=6,decimal_places=2,default=Decimal("0"))
    overtime_minutes=models.PositiveIntegerField(default=0)
    basic=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    allowances=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    incentives=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    commission_amount=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    bonus_amount=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    overtime_amount=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    deductions=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    adjustment_amount=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    advance_recovery=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    gross=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    net=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    status=models.CharField(max_length=30,default="Generated")
    paid_amount=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    payment_status=models.CharField(max_length=30,default="Unpaid")
    payment_history=models.JSONField(default=list,blank=True)
    calculation_snapshot=models.JSONField(default=dict,blank=True)

    class Meta:
        constraints=[
            models.UniqueConstraint(fields=["payroll_run","employee"],name="unique_payslip_employee_run"),
        ]


class PayrollLineItem(CompanyOwnedModel):
    payslip=models.ForeignKey(Payslip,on_delete=models.CASCADE,related_name="line_items")
    kind=models.CharField(max_length=20,default="earning")
    code=models.CharField(max_length=60)
    description=models.CharField(max_length=180)
    quantity=models.DecimalField(max_digits=12,decimal_places=4,default=Decimal("1"))
    rate=models.DecimalField(max_digits=12,decimal_places=4,default=Decimal("0"))
    amount=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    source_type=models.CharField(max_length=60,blank=True)
    source_id=models.CharField(max_length=120,blank=True)
    source_key=models.CharField(max_length=180,blank=True)
    metadata=models.JSONField(default=dict,blank=True)

    class Meta:
        ordering=["kind","code"]
        constraints=[
            models.UniqueConstraint(
                fields=["payslip","source_key"],
                condition=~Q(source_key=""),
                name="unique_payroll_line_source",
            ),
        ]


class PayrollAdjustment(CompanyOwnedModel):
    payslip=models.ForeignKey(Payslip,on_delete=models.CASCADE,related_name="adjustments")
    adjustment_type=models.CharField(max_length=30,default="earning")
    code=models.CharField(max_length=60,default="MANUAL")
    description=models.CharField(max_length=180)
    amount=models.DecimalField(max_digits=12,decimal_places=2)
    reason=models.TextField(blank=True)
    status=models.CharField(max_length=30,default="Pending")
    approved_by=models.ForeignKey("accounts.User",on_delete=models.SET_NULL,null=True,blank=True,related_name="approved_payroll_adjustments")
    approved_at=models.DateTimeField(null=True,blank=True)

    class Meta:
        ordering=["-created_at"]


class SalaryPayment(CompanyOwnedModel):
    payslip=models.ForeignKey(Payslip,on_delete=models.PROTECT,related_name="salary_payments")
    employee=models.ForeignKey("employees.Employee",on_delete=models.PROTECT,related_name="salary_payments")
    payment_date=models.DateField()
    amount=models.DecimalField(max_digits=12,decimal_places=2)
    method=models.CharField(max_length=40,default="Bank Transfer")
    reference=models.CharField(max_length=120,blank=True)
    status=models.CharField(max_length=30,default="Successful")
    remarks=models.TextField(blank=True)
    recorded_by=models.ForeignKey("accounts.User",on_delete=models.SET_NULL,null=True,blank=True,related_name="recorded_salary_payments")

    class Meta:
        ordering=["-payment_date","-created_at"]


class Incentive(CompanyOwnedModel):
    employee=models.ForeignKey("employees.Employee",on_delete=models.CASCADE,related_name="incentives")
    incentive_type=models.CharField(max_length=60,default="Commission")
    source=models.CharField(max_length=120,blank=True)
    reference=models.CharField(max_length=120,blank=True)
    completion_date=models.DateField()
    payroll_month=models.CharField(max_length=30,blank=True)
    amount=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    status=models.CharField(max_length=30,default="Pending")
    approved_by=models.ForeignKey("accounts.User",on_delete=models.SET_NULL,null=True,blank=True,related_name="approved_incentives")
    approved_at=models.DateTimeField(null=True,blank=True)
    notes=models.TextField(blank=True)
    metadata=models.JSONField(default=dict,blank=True)

    class Meta:
        ordering=["-completion_date","-created_at"]
