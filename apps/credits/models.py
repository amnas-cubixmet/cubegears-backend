"""Company wallet accounting. Never store financial balances in the browser."""
import uuid
from decimal import Decimal
from django.db import models
from django.utils import timezone


class CreditWallet(models.Model):
    company = models.OneToOneField("companies.Company", on_delete=models.CASCADE, related_name="credit_wallet")
    balance = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal("0.00"))
    low_threshold = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("100.00"))
    updated_at = models.DateTimeField(auto_now=True)


class CreditPackage(models.Model):
    name = models.CharField(max_length=120)
    credits = models.DecimalField(max_digits=12, decimal_places=2)
    price_inr = models.DecimalField(max_digits=12, decimal_places=2)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]


class FeatureCreditPrice(models.Model):
    feature = models.OneToOneField("saas.PlanFeature", on_delete=models.PROTECT, related_name="credit_price")
    base_credits = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    per_staff_credits = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    activation_credits = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    per_user_credits = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    billing_period = models.CharField(max_length=8, choices=[("daily", "Daily"), ("weekly", "Weekly"), ("monthly", "30 days")], default="monthly")
    is_available = models.BooleanField(default=True)


class CompanyCreditFeature(models.Model):
    company = models.ForeignKey("companies.Company", on_delete=models.CASCADE, related_name="credit_features")
    feature = models.ForeignKey("saas.PlanFeature", on_delete=models.PROTECT)
    enabled = models.BooleanField(default=False)
    next_charge_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["company", "feature"], name="credits_unique_company_feature")]


class CreditLedger(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey("companies.Company", on_delete=models.CASCADE, related_name="credit_events")
    kind = models.CharField(max_length=20, db_index=True)
    description = models.CharField(max_length=255)
    delta = models.DecimalField(max_digits=16, decimal_places=2)
    balance_after = models.DecimalField(max_digits=16, decimal_places=2)
    reference = models.CharField(max_length=180, unique=True)
    actor = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["company", "created_at"], name="credit_ledger_company_dt")]


class CreditPurchase(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey("companies.Company", on_delete=models.CASCADE, related_name="credit_purchases")
    package = models.ForeignKey(CreditPackage, on_delete=models.PROTECT)
    requested_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL)
    credits = models.DecimalField(max_digits=12, decimal_places=2)
    amount_paise = models.PositiveBigIntegerField()
    status = models.CharField(max_length=16, default="pending", db_index=True)
    receipt = models.CharField(max_length=40, unique=True)
    order_id = models.CharField(max_length=80, unique=True, null=True, blank=True)
    payment_link_id = models.CharField(max_length=80, unique=True, null=True, blank=True)
    checkout_url = models.URLField(max_length=500, blank=True)
    payment_id = models.CharField(max_length=80, unique=True, null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    paid_at = models.DateTimeField(null=True, blank=True)


class CreditWebhookEvent(models.Model):
    event_id = models.CharField(max_length=150, unique=True)
    event_type = models.CharField(max_length=100)
    received_at = models.DateTimeField(auto_now_add=True)


class FeatureBundle(models.Model):
    """One paid label that reuses multiple existing platform features."""
    PERIODS = [
        ("permanent", "Permanent"),
        ("weekly", "Weekly (7 days)"),
        ("monthly", "Monthly (30 days)"),
        ("daily", "Daily (24 hours)"),
        ("hourly", "Hourly (1 hour)"),
        ("per_use", "Per use (one prepaid operation)"),
    ]
    key = models.SlugField(max_length=100, unique=True)
    name = models.CharField(max_length=120)
    description = models.CharField(max_length=400, blank=True)
    features = models.ManyToManyField("saas.PlanFeature", related_name="credit_bundles")
    price_credits = models.DecimalField(max_digits=12, decimal_places=2)
    activation_credits = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    per_user_credits = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    billing_type = models.CharField(max_length=15, choices=PERIODS, default="weekly")
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class CompanyBundleAccess(models.Model):
    """Company-scoped, prepaid entitlement. Prices are snapshotted on purchase."""
    company = models.ForeignKey(
        "companies.Company", on_delete=models.CASCADE, related_name="bundle_access"
    )
    bundle = models.ForeignKey(
        FeatureBundle, on_delete=models.PROTECT, related_name="company_access"
    )
    enabled = models.BooleanField(default=True)
    valid_until = models.DateTimeField(null=True, blank=True)
    uses_remaining = models.PositiveIntegerField(default=0)
    last_price_credits = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    billing_type_at_purchase = models.CharField(
        max_length=15, choices=FeatureBundle.PERIODS, default="weekly"
    )
    purchased_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "bundle"], name="credits_unique_company_bundle"
            )
        ]


class StorageCreditPolicy(models.Model):
    """Global metered storage policy; one published rule for all tenant wallets."""
    billing_period = models.CharField(
        max_length=8, choices=[("daily", "Daily"), ("monthly", "Monthly")],
        default="monthly",
    )
    included_gb = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal("0"))
    credits_per_gb = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    is_active = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return "Storage wallet billing policy"


class PostpaidFeatureState(models.Model):
    """Opt-in monthly usage mode for one company's explicitly activated feature."""
    company = models.ForeignKey("companies.Company", on_delete=models.CASCADE, related_name="postpaid_feature_states")
    feature = models.ForeignKey("saas.PlanFeature", on_delete=models.PROTECT, related_name="postpaid_states")
    enabled = models.BooleanField(default=False)
    meter_started_at = models.DateTimeField(null=True, blank=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    base_credits = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    per_user_credits = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    per_staff_credits = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["company", "feature"], name="credit_postpaid_unique_company_feature")
        ]


class PostpaidUsageRecord(models.Model):
    """Immutable charge accrual; no wallet debit until the monthly settlement."""
    company = models.ForeignKey("companies.Company", on_delete=models.CASCADE, related_name="postpaid_usage")
    feature = models.ForeignKey("saas.PlanFeature", null=True, blank=True, on_delete=models.PROTECT)
    kind = models.CharField(max_length=32, choices=[
        ("feature_usage", "Feature usage"), ("feature_activation", "Feature activation")
    ])
    period = models.CharField(max_length=7, db_index=True)  # YYYY-MM in configured timezone
    from_at = models.DateTimeField()
    until_at = models.DateTimeField()
    user_count = models.PositiveIntegerField(default=0)
    staff_count = models.PositiveIntegerField(default=0)
    credits = models.DecimalField(max_digits=18, decimal_places=8, default=Decimal("0"))
    reference = models.CharField(max_length=180, unique=True)
    recorded_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["from_at", "id"]
        indexes = [models.Index(fields=["company", "period", "kind"], name="credit_postpaid_usage_period")]


class StorageMeterSample(models.Model):
    """Server-observed storage at a scheduled cycle; never browser-supplied."""
    company = models.ForeignKey("companies.Company", on_delete=models.CASCADE, related_name="storage_meter_samples")
    sampled_at = models.DateTimeField(default=timezone.now)
    bytes_used = models.PositiveBigIntegerField(default=0)
    file_count = models.PositiveIntegerField(default=0)
    reference = models.CharField(max_length=180, unique=True)

    class Meta:
        ordering = ["-sampled_at"]
        indexes = [models.Index(fields=["company", "sampled_at"], name="credit_storage_sample_dt")]


class MonthlyCreditStatement(models.Model):
    """One immutable invoice snapshot per company and calendar month."""
    company = models.ForeignKey("companies.Company", on_delete=models.CASCADE, related_name="monthly_credit_statements")
    period = models.CharField(max_length=7)
    feature_credits = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal("0"))
    storage_credits = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal("0"))
    total_credits = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal("0"))
    details = models.JSONField(default=dict)
    status = models.CharField(max_length=16, default="pending", choices=[
        ("pending", "Pending"), ("paid", "Paid"),
    ])
    created_at = models.DateTimeField(default=timezone.now)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["company", "period"], name="credit_monthly_company_period")
        ]
        ordering = ["-period", "-id"]
