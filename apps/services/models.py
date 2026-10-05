from decimal import Decimal
from django.db import models
from common.models import CompanyOwnedModel

class ServiceCategory(CompanyOwnedModel):
    name=models.CharField(max_length=120)
    description=models.TextField(blank=True)
    is_active=models.BooleanField(default=True)
    class Meta: ordering=["name"]
    def __str__(self): return self.name

class Service(CompanyOwnedModel):
    category=models.ForeignKey(ServiceCategory,on_delete=models.SET_NULL,null=True,blank=True,related_name="services")
    name=models.CharField(max_length=160)
    code=models.CharField(max_length=40,blank=True)
    description=models.TextField(blank=True)
    labour_hours=models.DecimalField(max_digits=6,decimal_places=2,default=Decimal("0"))
    price=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    tax_rate=models.DecimalField(max_digits=5,decimal_places=2,default=Decimal("18"))
    hsn_sac=models.CharField(max_length=20,blank=True)
    is_active=models.BooleanField(default=True)
    class Meta:
        ordering=["name"]
        constraints=[models.UniqueConstraint(fields=["company","code"],name="unique_service_code_per_company")]
    def __str__(self): return self.name
