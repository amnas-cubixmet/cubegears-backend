from decimal import Decimal
from django.db import models
from django.utils import timezone
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
    plan_definition=models.ForeignKey("saas.SubscriptionPlan",null=True,blank=True,on_delete=models.PROTECT,related_name="subscriptions")

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

class MediaFile(CompanyOwnedModel):
    file=models.FileField(upload_to="saas/%Y/%m/")
    name=models.CharField(max_length=255)
    category=models.CharField(max_length=80,default="General")
    mime_type=models.CharField(max_length=120,blank=True)
    size_bytes=models.BigIntegerField(default=0)
    uploaded_by=models.ForeignKey("accounts.User",on_delete=models.SET_NULL,null=True,blank=True,related_name="uploaded_media_files")
    class Meta: ordering=["-created_at"]


class FeatureGroup(models.Model):
    """Editable groups and nested labels in the platform plan builder."""
    key = models.SlugField(max_length=100, unique=True)
    label = models.CharField(max_length=120)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children")
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "label"]

    def __str__(self):
        return self.label


class PlanFeature(models.Model):
    key = models.SlugField(max_length=100, unique=True)
    label = models.CharField(max_length=160)
    group = models.ForeignKey(FeatureGroup, on_delete=models.PROTECT, related_name="features")
    description = models.CharField(max_length=255, blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "label"]

    def __str__(self):
        return self.label


class SubscriptionPlan(models.Model):
    key = models.SlugField(max_length=80, unique=True)
    name = models.CharField(max_length=120)
    description = models.CharField(max_length=400, blank=True)
    monthly_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    yearly_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    promo_percent = models.PositiveSmallIntegerField(default=0)
    promo_ends_at = models.DateTimeField(null=True, blank=True)
    max_branches = models.PositiveIntegerField(default=1)
    max_users = models.PositiveIntegerField(default=3)
    included_storage_gb = models.PositiveIntegerField(default=5)
    extra_storage_rate = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("15"))
    trial_days = models.PositiveIntegerField(default=14)
    access_duration_days = models.PositiveIntegerField(default=180, help_text='Days of access after each verified payment (e.g. 180 or 210).')
    checkout_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"), help_text="One-time Razorpay payment for the configured access duration.")
    is_public = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    features = models.ManyToManyField(PlanFeature, through="PlanFeatureAccess", related_name="plans")

    class Meta:
        ordering = ["sort_order", "monthly_price", "name"]

    def __str__(self):
        return self.name

    def price_for(self, cycle):
        amount = self.yearly_price if cycle == "yearly" else self.monthly_price
        if self.promo_percent and (not self.promo_ends_at or self.promo_ends_at > timezone.now()):
            return (amount * (Decimal("100") - self.promo_percent) / Decimal("100")).quantize(Decimal("0.01"))
        return amount


class PlanFeatureAccess(models.Model):
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.CASCADE, related_name="feature_access")
    feature = models.ForeignKey(PlanFeature, on_delete=models.CASCADE, related_name="plan_access")
    enabled = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["plan", "feature"], name="unique_plan_feature_access")]


class PlanChangeRequest(models.Model):
    STATUS = [("pending", "Pending"), ("contacted", "Contacted"), ("approved", "Approved"), ("rejected", "Rejected")]
    company = models.ForeignKey("companies.Company", on_delete=models.CASCADE, related_name="plan_change_requests")
    requested_by = models.ForeignKey("accounts.User", null=True, on_delete=models.SET_NULL)
    requested_plan = models.ForeignKey(SubscriptionPlan, on_delete=models.PROTECT)
    billing_cycle = models.CharField(max_length=10, choices=[("term", "One-time access period")])
    status = models.CharField(max_length=20, choices=STATUS, default="pending")
    message = models.TextField(blank=True, max_length=2000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class CustomPlanRequest(models.Model):
    company = models.ForeignKey("companies.Company", on_delete=models.CASCADE, related_name="custom_plan_requests")
    requested_by = models.ForeignKey("accounts.User", null=True, on_delete=models.SET_NULL)
    email = models.EmailField()
    message = models.TextField(max_length=4000)
    branches = models.PositiveIntegerField(default=1)
    users = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=20, choices=PlanChangeRequest.STATUS, default="pending")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

class RazorpayPlanPayment(CompanyOwnedModel):
    """Order amount/duration immutable from client; only captured payments activate access."""
    STATUS = [("pending", "Pending"), ("captured", "Captured"), ("failed", "Failed"), ("refunded", "Refunded")]
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.PROTECT, related_name="razorpay_payments")
    requested_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True, blank=True)
    subscription = models.ForeignKey(Subscription, on_delete=models.SET_NULL, null=True, blank=True, related_name="razorpay_payments")
    cycle = models.CharField(max_length=10, choices=[("monthly", "Monthly"), ("yearly", "Yearly")])
    validity_days = models.PositiveIntegerField()
    amount_paise = models.PositiveBigIntegerField()
    currency = models.CharField(max_length=3, default="INR")
    receipt = models.CharField(max_length=40, unique=True)
    razorpay_order_id = models.CharField(max_length=80, unique=True, null=True, blank=True)
    razorpay_payment_id = models.CharField(max_length=80, unique=True, null=True, blank=True)
    status = models.CharField(max_length=15, choices=STATUS, default="pending", db_index=True)
    payment_method = models.CharField(max_length=30, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    period_start = models.DateTimeField(null=True, blank=True)
    period_end = models.DateTimeField(null=True, blank=True)

    @property
    def amount_inr(self):
        return Decimal(self.amount_paise) / Decimal(100)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["company", "status"], name="saas_rp_company_status")]


class RazorpayWebhookDelivery(models.Model):
    event_id = models.CharField(max_length=150, unique=True)
    event_type = models.CharField(max_length=100)
    received_at = models.DateTimeField(auto_now_add=True)
