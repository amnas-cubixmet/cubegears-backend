from decimal import Decimal
from django.db import models
from common.models import CompanyOwnedModel

class SalaryStructure(CompanyOwnedModel):
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

class PayrollRun(CompanyOwnedModel):
    month=models.PositiveSmallIntegerField()
    year=models.PositiveSmallIntegerField()
    status=models.CharField(max_length=30,default="Draft")
    processed_at=models.DateTimeField(null=True,blank=True)
    processed_by=models.ForeignKey("accounts.User",on_delete=models.SET_NULL,null=True,blank=True)
    class Meta:
        ordering=["-year","-month"]
        constraints=[models.UniqueConstraint(fields=["company","month","year"],name="unique_payroll_period_per_company")]

class Payslip(CompanyOwnedModel):
    payroll_run=models.ForeignKey(PayrollRun,on_delete=models.CASCADE,related_name="payslips")
    employee=models.ForeignKey("employees.Employee",on_delete=models.PROTECT,related_name="payslips")
    attendance_days=models.DecimalField(max_digits=6,decimal_places=2,default=Decimal("0"))
    overtime_minutes=models.PositiveIntegerField(default=0)
    basic=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    allowances=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    incentives=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    overtime_amount=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    deductions=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    advance_recovery=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    gross=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    net=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    status=models.CharField(max_length=30,default="Generated")
    paid_amount=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    payment_status=models.CharField(max_length=30,default="Unpaid")
    payment_history=models.JSONField(default=list,blank=True)
