from decimal import Decimal
from django.db import models
from common.models import CompanyOwnedModel
class Expense(CompanyOwnedModel):
    date=models.DateField()
    category=models.CharField(max_length=100)
    vendor=models.CharField(max_length=180,blank=True)
    description=models.TextField(blank=True)
    amount=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    tax=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    payment_method=models.CharField(max_length=50,blank=True)
    reference=models.CharField(max_length=120,blank=True)
    attachment=models.URLField(blank=True)
    status=models.CharField(max_length=30,default="Paid")
    created_by=models.ForeignKey("accounts.User",on_delete=models.SET_NULL,null=True,blank=True)
    class Meta: ordering=["-date","-created_at"]
