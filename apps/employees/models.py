from decimal import Decimal
from django.db import models
from common.models import CompanyOwnedModel

class Team(CompanyOwnedModel):
    name=models.CharField(max_length=120)
    lead=models.ForeignKey("accounts.User",on_delete=models.SET_NULL,null=True,blank=True,related_name="led_teams")
    description=models.TextField(blank=True)
    is_active=models.BooleanField(default=True)

class Shift(CompanyOwnedModel):
    name=models.CharField(max_length=120)
    start_time=models.TimeField()
    end_time=models.TimeField()
    break_minutes=models.PositiveIntegerField(default=0)
    weekly_off=models.JSONField(default=list,blank=True)
    is_active=models.BooleanField(default=True)

class Skill(CompanyOwnedModel):
    name=models.CharField(max_length=120)
    category=models.CharField(max_length=100,blank=True)
    is_active=models.BooleanField(default=True)

class Employee(CompanyOwnedModel):
    user=models.OneToOneField("accounts.User",on_delete=models.SET_NULL,null=True,blank=True,related_name="employee_profile")
    employee_code=models.CharField(max_length=40)
    name=models.CharField(max_length=160)
    phone=models.CharField(max_length=30,blank=True)
    email=models.EmailField(blank=True)
    designation=models.CharField(max_length=100,blank=True)
    role_name=models.CharField(max_length=100,blank=True)
    department_name=models.CharField(max_length=120,blank=True)
    shift_label=models.CharField(max_length=160,blank=True)
    payment_type=models.CharField(max_length=80,blank=True)
    notes=models.TextField(blank=True)
    team=models.ForeignKey(Team,on_delete=models.SET_NULL,null=True,blank=True,related_name="employees")
    shift=models.ForeignKey(Shift,on_delete=models.SET_NULL,null=True,blank=True,related_name="employees")
    skills=models.ManyToManyField(Skill,blank=True,related_name="employees")
    joining_date=models.DateField(null=True,blank=True)
    employment_type=models.CharField(max_length=50,default="Full Time")
    base_salary=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    status=models.CharField(max_length=20,default="Active")
    address=models.TextField(blank=True)
    emergency_contact=models.CharField(max_length=120,blank=True)
    class Meta:
        ordering=["name"]
        constraints=[models.UniqueConstraint(fields=["company","employee_code"],name="unique_employee_code_per_company")]

class EmployeeDocument(CompanyOwnedModel):
    employee=models.ForeignKey(Employee,on_delete=models.CASCADE,related_name="documents")
    document_type=models.CharField(max_length=80)
    title=models.CharField(max_length=180)
    file_url=models.URLField()
    expiry_date=models.DateField(null=True,blank=True)
    notes=models.TextField(blank=True)


class EmployeeActivity(CompanyOwnedModel):
    employee=models.ForeignKey(Employee,on_delete=models.CASCADE,related_name="activities")
    action=models.CharField(max_length=120)
    details=models.TextField(blank=True)
    actor=models.ForeignKey("accounts.User",on_delete=models.SET_NULL,null=True,blank=True,related_name="employee_activity_entries")
    metadata=models.JSONField(default=dict,blank=True)

    class Meta:
        ordering=["-created_at"]
