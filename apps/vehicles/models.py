from django.db import models
from common.models import CompanyOwnedModel

class Vehicle(CompanyOwnedModel):
    customer=models.ForeignKey("customers.Customer",on_delete=models.CASCADE,related_name="vehicles")
    registration=models.CharField(max_length=30)
    make=models.CharField(max_length=80,blank=True)
    model=models.CharField(max_length=80,blank=True)
    variant=models.CharField(max_length=80,blank=True)
    year=models.PositiveIntegerField(null=True,blank=True)
    fuel_type=models.CharField(max_length=30,blank=True)
    transmission=models.CharField(max_length=30,blank=True)
    color=models.CharField(max_length=40,blank=True)
    odometer=models.PositiveIntegerField(default=0)
    vin=models.CharField(max_length=64,blank=True)
    engine_no=models.CharField(max_length=64,blank=True)
    last_service_date=models.DateField(null=True,blank=True)
    next_service_due=models.DateField(null=True,blank=True)
    insurance_expiry=models.DateField(null=True,blank=True)
    notes=models.TextField(blank=True)
    status=models.CharField(max_length=20,default="Active")

    class Meta:
        ordering=["registration"]
        constraints=[models.UniqueConstraint(fields=["company","registration"],name="unique_vehicle_registration_per_company")]

    def __str__(self): return self.registration
