from decimal import Decimal
from django.db import models
from common.models import CompanyOwnedModel

class Customer(CompanyOwnedModel):
    TYPE_CHOICES = [("individual","Individual"),("business","Business")]
    STATUS_CHOICES = [("active","Active"),("archived","Archived")]
    name = models.CharField(max_length=160)
    phone = models.CharField(max_length=30)
    whatsapp = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    customer_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default="individual")
    company_name = models.CharField(max_length=180, blank=True)
    gstin = models.CharField(max_length=20, blank=True)
    address = models.TextField(blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    pincode = models.CharField(max_length=12, blank=True)
    notes = models.TextField(blank=True)
    credit_limit = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="active")
    last_service_date = models.DateField(null=True, blank=True)
    next_reminder_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["company","phone"]), models.Index(fields=["company","email"])]

    def __str__(self): return self.name

class CustomerActivity(CompanyOwnedModel):
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="activities")
    activity_type = models.CharField(max_length=80)
    description = models.TextField()
    actor = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]

class CustomerReminder(CompanyOwnedModel):
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="reminders")
    title = models.CharField(max_length=160)
    message = models.TextField(blank=True)
    due_at = models.DateTimeField()
    status = models.CharField(max_length=20, default="pending")
    channel = models.CharField(max_length=30, default="internal")
