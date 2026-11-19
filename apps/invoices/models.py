from decimal import Decimal
from django.db import models
from common.models import CompanyOwnedModel

class QuickService(CompanyOwnedModel):
    description = models.CharField(max_length=255)
    start_key = models.UUIDField()
    start_fingerprint = models.CharField(max_length=64, blank=True, default="")
    started_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True)
    customer = models.ForeignKey(
        "customers.Customer", on_delete=models.PROTECT, null=True, blank=True,
        related_name="quick_services",
    )
    vehicle = models.ForeignKey(
        "vehicles.Vehicle", on_delete=models.PROTECT, null=True, blank=True,
        related_name="quick_services",
    )
    items = models.JSONField(default=list, blank=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["company", "start_key"], name="unique_quick_service_start"),
        ]


class Invoice(CompanyOwnedModel):
    KIND=[("invoice","Invoice"),("estimate","Estimate")]
    number=models.CharField(max_length=50)
    kind=models.CharField(max_length=20,choices=KIND,default="invoice")
    invoice_type=models.CharField(max_length=20,default="regular")
    status=models.CharField(max_length=30,default="Draft")
    date=models.DateField()
    customer=models.ForeignKey("customers.Customer",on_delete=models.PROTECT,related_name="invoices",null=True)
    quick_service=models.OneToOneField(QuickService,on_delete=models.PROTECT,related_name="invoice",null=True,blank=True)
    vehicle=models.ForeignKey("vehicles.Vehicle",on_delete=models.SET_NULL,null=True,blank=True,related_name="invoices")
    job=models.ForeignKey("jobs.Job",on_delete=models.SET_NULL,null=True,blank=True,related_name="invoices")
    seller_snapshot=models.JSONField(default=dict,blank=True)
    template_snapshot=models.JSONField(default=dict,blank=True)
    customer_snapshot=models.JSONField(default=dict,blank=True)
    vehicle_snapshot=models.JSONField(default=dict,blank=True)
    transport=models.JSONField(default=dict,blank=True)
    tax_mode=models.CharField(max_length=20,default="none")
    cgst_rate=models.DecimalField(max_digits=5,decimal_places=2,default=Decimal("0"))
    sgst_rate=models.DecimalField(max_digits=5,decimal_places=2,default=Decimal("0"))
    igst_rate=models.DecimalField(max_digits=5,decimal_places=2,default=Decimal("0"))
    payment_type=models.CharField(max_length=30,blank=True)
    terms_conditions=models.TextField(blank=True)
    staff_name=models.CharField(max_length=160,blank=True)
    source_job_id=models.CharField(max_length=80,blank=True)
    notes=models.TextField(blank=True)
    discount=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    adjustment=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    taxable=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    cgst=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    sgst=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    igst=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    total=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    paid=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    balance=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    payment_mode=models.CharField(max_length=50,blank=True)
    payment_terms=models.CharField(max_length=100,blank=True)
    source_estimate=models.ForeignKey("self",on_delete=models.SET_NULL,null=True,blank=True,related_name="converted_invoices")
    finalized_at=models.DateTimeField(null=True,blank=True)
    cancelled_at=models.DateTimeField(null=True,blank=True)
    cancellation_reason=models.CharField(max_length=500,blank=True,default="")
    class Meta:
        ordering=["-date","-created_at"]
        constraints=[
            models.UniqueConstraint(fields=["company","number"],name="unique_invoice_number_per_company"),
            models.CheckConstraint(
                condition=(
                    models.Q(quick_service__isnull=True, customer__isnull=False)
                    | (
                        models.Q(quick_service__isnull=False, job__isnull=True, kind="invoice")
                        & (
                            models.Q(customer__isnull=True, vehicle__isnull=True)
                            | models.Q(customer__isnull=False)
                        )
                    )
                ),
                name="invoice_recipient_or_quick_service",
            ),
        ]
    def __str__(self): return self.number

class InvoiceItem(models.Model):
    id=models.BigAutoField(primary_key=True)
    invoice=models.ForeignKey(Invoice,on_delete=models.CASCADE,related_name="items")
    item_type=models.CharField(max_length=30,default="Custom Item")
    description=models.CharField(max_length=255)
    code=models.CharField(max_length=80,blank=True)
    hsn_sac=models.CharField(max_length=30,blank=True)
    quantity=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("1"))
    unit=models.CharField(max_length=20,default="PCS")
    purchase_price=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    rate=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    tax_rate=models.DecimalField(max_digits=5,decimal_places=2,default=Decimal("0"))
    tax_choice=models.CharField(max_length=16,blank=True,default="")
    discount=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    stock_item=models.ForeignKey("inventory.StockItem",on_delete=models.SET_NULL,null=True,blank=True)

class EWayBill(CompanyOwnedModel):
    invoice=models.ForeignKey(Invoice,on_delete=models.CASCADE,null=True,blank=True,related_name="eway_bills")
    number=models.CharField(max_length=50,blank=True)
    status=models.CharField(max_length=30,default="Draft")
    vehicle_no=models.CharField(max_length=30,blank=True)
    transporter_name=models.CharField(max_length=180,blank=True)
    transporter_id=models.CharField(max_length=50,blank=True)
    distance_km=models.PositiveIntegerField(default=0)
    payload=models.JSONField(default=dict,blank=True)
