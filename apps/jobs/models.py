from decimal import Decimal
from django.db import models
from common.models import CompanyOwnedModel

class Job(CompanyOwnedModel):
    STATUS_NEW="New"; STATUS_INSPECTION="Inspection"; STATUS_ESTIMATE_PENDING="Estimate Pending"; STATUS_APPROVED="Approved"
    STATUS_IN_PROGRESS="In Progress"; STATUS_WAITING_PARTS="Waiting for Parts"; STATUS_QC="QC"; STATUS_READY="Ready for Delivery"; STATUS_DELIVERED="Delivered"
    STATUS_CHOICES=[(x,x) for x in [STATUS_NEW,STATUS_INSPECTION,STATUS_ESTIMATE_PENDING,STATUS_APPROVED,STATUS_IN_PROGRESS,STATUS_WAITING_PARTS,STATUS_QC,STATUS_READY,STATUS_DELIVERED]]
    job_number=models.CharField(max_length=40)
    customer=models.ForeignKey("customers.Customer",on_delete=models.PROTECT,related_name="jobs")
    vehicle=models.ForeignKey("vehicles.Vehicle",on_delete=models.PROTECT,related_name="jobs")
    advisor=models.ForeignKey("accounts.User",on_delete=models.SET_NULL,null=True,blank=True,related_name="advised_jobs")
    technician=models.ForeignKey("accounts.User",on_delete=models.SET_NULL,null=True,blank=True,related_name="assigned_jobs")
    status=models.CharField(max_length=40,choices=STATUS_CHOICES,default=STATUS_NEW)
    priority=models.CharField(max_length=20,default="Normal")
    odometer=models.PositiveIntegerField(default=0)
    fuel_level=models.CharField(max_length=30,blank=True)
    promised_at=models.DateTimeField(null=True,blank=True)
    delivered_at=models.DateTimeField(null=True,blank=True)
    complaints=models.JSONField(default=list,blank=True)
    inspection=models.JSONField(default=dict,blank=True)
    work=models.JSONField(default=list,blank=True)
    qc=models.JSONField(default=dict,blank=True)
    workflow_progress=models.JSONField(default=dict,blank=True)
    parts_workflow=models.JSONField(default=dict,blank=True)
    notes=models.TextField(blank=True)
    estimate_total=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    labour_total=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    parts_total=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    customer_rating=models.PositiveSmallIntegerField(null=True,blank=True)
    customer_feedback=models.TextField(blank=True)
    class Meta:
        ordering=["-created_at"]
        constraints=[models.UniqueConstraint(fields=["company","job_number"],name="unique_job_number_per_company")]
    def __str__(self): return self.job_number

class JobEstimate(CompanyOwnedModel):
    job=models.ForeignKey(Job,on_delete=models.CASCADE,related_name="estimates")
    version=models.PositiveIntegerField(default=1)
    status=models.CharField(max_length=30,default="Draft")
    items=models.JSONField(default=list,blank=True)
    subtotal=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    tax=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    total=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    approved_at=models.DateTimeField(null=True,blank=True)

class JobPart(CompanyOwnedModel):
    job=models.ForeignKey(Job,on_delete=models.CASCADE,related_name="parts")
    item=models.ForeignKey("inventory.StockItem",on_delete=models.PROTECT,related_name="job_parts")
    quantity=models.DecimalField(max_digits=10,decimal_places=2)
    unit_price=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    issued=models.BooleanField(default=False)

class JobPhoto(CompanyOwnedModel):
    job=models.ForeignKey(Job,on_delete=models.CASCADE,related_name="photos")
    url=models.URLField()
    photo_type=models.CharField(max_length=30,default="general")
    caption=models.CharField(max_length=255,blank=True)
    uploaded_by=models.ForeignKey("accounts.User",on_delete=models.SET_NULL,null=True,blank=True)

class JobActivity(CompanyOwnedModel):
    job=models.ForeignKey(Job,on_delete=models.CASCADE,related_name="activities")
    event=models.CharField(max_length=80)
    description=models.TextField(blank=True)
    actor=models.ForeignKey("accounts.User",on_delete=models.SET_NULL,null=True,blank=True)
    from_status=models.CharField(max_length=40,blank=True)
    to_status=models.CharField(max_length=40,blank=True)
    metadata=models.JSONField(default=dict,blank=True)
    class Meta: ordering=["-created_at"]



class OutsideLabourCharge(CompanyOwnedModel):
    """One-off freelance work; NOT an Employee or Payroll earning.

    The customer charge is a reference to the Job Card quote only.
    Paying the contractor creates one linked expense; it does not modify
    invoice labour totals or internal staff wages.
    """
    STATUS_PENDING = "Pending"
    STATUS_PAID = "Paid"
    job = models.ForeignKey(Job, on_delete=models.PROTECT, related_name="outside_labour")
    worker_name = models.CharField(max_length=160)
    worker_phone = models.CharField(max_length=30, blank=True)
    work_description = models.CharField(max_length=300)
    customer_charge = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    worker_charge = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(
        max_length=12, choices=[(STATUS_PENDING, "Pending"), (STATUS_PAID, "Paid")],
        default=STATUS_PENDING,
    )
    payment_method = models.CharField(max_length=40, blank=True)
    payment_reference = models.CharField(max_length=120, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="created_outside_labour",
    )
    paid_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="paid_outside_labour",
    )
    expense = models.OneToOneField(
        "expenses.Expense", on_delete=models.PROTECT, null=True, blank=True,
        related_name="outside_labour",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["company", "status"], name="outlab_company_status_idx")]
