from decimal import Decimal
from django.db import models
from django.core.exceptions import ValidationError
from common.models import CompanyOwnedModel

class ServiceCategory(CompanyOwnedModel):
    parent=models.ForeignKey("self",null=True,blank=True,on_delete=models.PROTECT,related_name="children")
    name=models.CharField(max_length=120)
    description=models.TextField(blank=True)
    types=models.JSONField(default=list,blank=True)
    is_active=models.BooleanField(default=True)
    class Meta: ordering=["name"]
    def clean(self):
        super().clean()
        seen = {self.pk} if self.pk else set()
        ancestor = self.parent
        while ancestor is not None:
            if ancestor.pk in seen:
                raise ValidationError({"parent": "A category cannot be its own ancestor."})
            if ancestor.company_id != self.company_id:
                raise ValidationError({"parent": "A parent category must belong to the same workshop."})
            seen.add(ancestor.pk)
            ancestor = ancestor.parent

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)

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
    unit=models.CharField(max_length=30,default="Job")
    pricing_type=models.CharField(max_length=30,default="Fixed")
    checklist=models.JSONField(default=list,blank=True)
    tax_treatment=models.CharField(max_length=20,default="taxable",
        choices=[(key,key) for key in ("taxable","exempt","nil_rated","non_gst","out_of_scope")])
    is_active=models.BooleanField(default=True)
    class Meta:
        ordering=["name"]
        constraints=[models.UniqueConstraint(fields=["company","code"],name="unique_service_code_per_company")]
    def __str__(self): return self.name
