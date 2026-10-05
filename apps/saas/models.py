from decimal import Decimal
from django.db import models
from common.models import CompanyOwnedModel

class Subscription(CompanyOwnedModel):
    plan=models.CharField(max_length=50,default="Starter")
    status=models.CharField(max_length=30,default="Active")
    billing_cycle=models.CharField(max_length=20,default="monthly")
    amount=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    currency=models.CharField(max_length=8,default="INR")
    starts_at=models.DateTimeField(null=True,blank=True)
    renews_at=models.DateTimeField(null=True,blank=True)
    seats=models.PositiveIntegerField(default=5)

class StorageUsage(CompanyOwnedModel):
    date=models.DateField()
    category=models.CharField(max_length=50,default="media")
    bytes_used=models.BigIntegerField(default=0)
    file_count=models.PositiveIntegerField(default=0)
    metadata=models.JSONField(default=dict,blank=True)
    class Meta: ordering=["-date"]

class DocumentTemplate(CompanyOwnedModel):
    TEMPLATE_TYPES=[(x,x) for x in ["invoice","estimate","job_card","receipt","payslip"]]
    name=models.CharField(max_length=140)
    template_type=models.CharField(max_length=40,choices=TEMPLATE_TYPES)
    logo_url=models.URLField(blank=True)
    header=models.JSONField(default=dict,blank=True)
    footer=models.JSONField(default=dict,blank=True)
    theme=models.JSONField(default=dict,blank=True)
    fields=models.JSONField(default=list,blank=True)
    is_default=models.BooleanField(default=False)
    is_active=models.BooleanField(default=True)

class CompanySetting(CompanyOwnedModel):
    category=models.CharField(max_length=50)
    data=models.JSONField(default=dict,blank=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=["company","category"],name="unique_company_setting_category")]

class SecurityEvent(CompanyOwnedModel):
    user=models.ForeignKey("accounts.User",on_delete=models.SET_NULL,null=True,blank=True)
    event=models.CharField(max_length=100)
    ip_address=models.GenericIPAddressField(null=True,blank=True)
    user_agent=models.TextField(blank=True)
    metadata=models.JSONField(default=dict,blank=True)
    class Meta: ordering=["-created_at"]
