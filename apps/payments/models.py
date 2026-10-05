from decimal import Decimal
from django.db import models
from common.models import CompanyOwnedModel

class Payment(CompanyOwnedModel):
    customer=models.ForeignKey("customers.Customer",on_delete=models.PROTECT,related_name="payments")
    invoice=models.ForeignKey("invoices.Invoice",on_delete=models.SET_NULL,null=True,blank=True,related_name="payments")
    date=models.DateField()
    amount=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    method=models.CharField(max_length=50,default="Cash")
    reference=models.CharField(max_length=120,blank=True)
    status=models.CharField(max_length=30,default="Completed")
    notes=models.TextField(blank=True)
    recorded_by=models.ForeignKey("accounts.User",on_delete=models.SET_NULL,null=True,blank=True)
    class Meta: ordering=["-date","-created_at"]
