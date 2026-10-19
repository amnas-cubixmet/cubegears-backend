"""Company wallet accounting. Never store financial balances in the browser."""
import uuid
from datetime import time
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
    # Archived packages remain available to historical purchases but cannot
    # be offered through top-up catalogues or reused accidentally.
    is_archived = models.BooleanField(default=False, db_index=True)
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
    billing_mode = models.CharField(
        max_length=16, choices=[
            ("snapshot", "Period snapshot (prepaid)"),
            ("metered_monthly", "Metered usage / monthly settlement"),
        ], default="snapshot",
    )
    meter_interval_seconds = models.PositiveIntegerField(default=3600)

    included_gb = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal("0"))
    credits_per_gb = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    is_active = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return "Storage wallet billing policy"


class CreditCyclePolicy(models.Model):
    """Opt-in global day-of-month wallet billing, independent of old plans.

    Activation is forward-only: existing bytes/time cannot be back-billed
    unless auditable events/sessions actually prove the observed interval.
    """
    day_of_month = models.PositiveSmallIntegerField(default=5)
    enabled = models.BooleanField(default=False)
    block_overdue = models.BooleanField(default=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class CreditCycleInvoice(models.Model):
    """One immutable-amount invoice per company/category/fixed monthly cycle."""
    company = models.ForeignKey("companies.Company", on_delete=models.CASCADE, related_name="cycle_invoices")
    category = models.CharField(max_length=12, choices=[("feature", "Feature"), ("storage", "Storage")])
    starts_on = models.DateField()
    ends_at = models.DateTimeField()
    due_at = models.DateTimeField()
    credits_due = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal("0"))
    status = models.CharField(max_length=14, choices=[("pending", "Pending"), ("paid", "Paid"), ("insufficient", "Insufficient credits")], default="pending")
    details = models.JSONField(default=dict, blank=True)
    settled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["company", "category", "starts_on"], name="unique_company_cycle_usage")]


class CycleLabelSession(models.Model):
    """Tenant activation, per-user rate and duration snapshot for postpaid labels."""
    company = models.ForeignKey("companies.Company", on_delete=models.CASCADE, related_name="cycle_label_sessions")
    bundle = models.ForeignKey("credits.FeatureBundle", on_delete=models.PROTECT, related_name="cycle_sessions")
    started_at = models.DateTimeField(default=timezone.now, db_index=True)
    ended_at = models.DateTimeField(null=True, blank=True, db_index=True)
    monthly_credits = models.DecimalField(max_digits=16, decimal_places=2)
    user_seats = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "bundle"], condition=models.Q(ended_at__isnull=True),
                name="unique_open_cycle_label",
            ),
        ]


class FeatureUsageSession(models.Model):
    """Immutable price snapshot for a postpaid feature's enabled interval."""
    company = models.ForeignKey("companies.Company", on_delete=models.CASCADE, related_name="feature_usage_sessions")
    feature = models.ForeignKey("saas.PlanFeature", on_delete=models.PROTECT, related_name="postpaid_sessions")
    started_at = models.DateTimeField(default=timezone.now, db_index=True)
    ended_at = models.DateTimeField(null=True, blank=True, db_index=True)
    monthly_credits = models.DecimalField(max_digits=16, decimal_places=2)
    activation_credits = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    user_seats = models.PositiveIntegerField(default=0)
    staff_seats = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "feature"], condition=models.Q(ended_at__isnull=True),
                name="credits_one_open_feature_usage",
            ),
        ]


class StorageUsageInterval(models.Model):
    """Measured bytes per fixed interval; a gap is never fabricated."""
    company = models.ForeignKey("companies.Company", on_delete=models.CASCADE, related_name="storage_meter_intervals")
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    billing_month = models.CharField(max_length=7, db_index=True)
    measured_bytes = models.PositiveBigIntegerField()
    credits_accrued = models.DecimalField(max_digits=18, decimal_places=6)
    rate_per_gb_month = models.DecimalField(max_digits=12, decimal_places=2)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "ends_at"], name="credits_unique_company_storage_tick",
            ),
        ]
        indexes = [models.Index(fields=["company", "billing_month"], name="credit_storage_company_mo")]


class MonthlyUsageInvoice(models.Model):
    """Month-end settlement for exactly one category and company."""
    company = models.ForeignKey("companies.Company", on_delete=models.CASCADE, related_name="monthly_usage_invoices")
    category = models.CharField(max_length=12, choices=[("feature", "Feature"), ("storage", "Storage")])
    billing_month = models.CharField(max_length=7)
    credits_due = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal("0.00"))
    status = models.CharField(max_length=14, choices=[
        ("pending", "Pending"), ("paid", "Paid"), ("insufficient", "Insufficient credits"),
    ], default="pending")
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    settled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["company", "category", "billing_month"],
                                    name="credits_unique_company_monthly_usage"),
        ]


# These models are created by credits migrations 0006_storage_monthly_metering
# and 0007_feature_monthly_metering. Keep runtime models synchronized with
# migration state so API imports and monthly metering are safe.
class StorageMeterCursor(models.Model):
    company = models.OneToOneField(
        "companies.Company", on_delete=models.CASCADE,
        related_name="storage_meter_cursor",
    )
    sampled_at = models.DateTimeField(null=True, blank=True)
    rate_duration_seconds = models.PositiveIntegerField(default=0)
    used_bytes = models.BigIntegerField(default=0)
    billable_gb = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal("0"))
    rate_per_gb = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    updated_at = models.DateTimeField(auto_now=True)


class StorageMeterPeriod(models.Model):
    company = models.ForeignKey(
        "companies.Company", on_delete=models.CASCADE, related_name="metered_storage_periods",
    )
    period = models.CharField(max_length=7)
    accrued_credits = models.DecimalField(max_digits=22, decimal_places=8, default=Decimal("0"))
    covered_seconds = models.PositiveBigIntegerField(default=0)
    settled_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "period"], name="storage_meter_unique_month",
            ),
        ]


class StorageMeterReading(models.Model):
    company = models.ForeignKey(
        "companies.Company", on_delete=models.CASCADE, related_name="storage_meter_readings",
    )
    sampled_at = models.DateTimeField()
    used_bytes = models.BigIntegerField(default=0)
    billable_gb = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal("0"))
    rate_per_gb = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    covered_seconds = models.PositiveIntegerField(default=0)
    accrued_credits = models.DecimalField(max_digits=22, decimal_places=8, default=Decimal("0"))

    class Meta:
        ordering = ["-sampled_at", "-pk"]
        indexes = [
            models.Index(fields=["company", "sampled_at"], name="storage_read_company_dt"),
        ]


class CompanyPostpaidFeature(models.Model):
    company = models.ForeignKey(
        "companies.Company", on_delete=models.CASCADE, related_name="postpaid_features",
    )
    feature = models.ForeignKey(
        "saas.PlanFeature", on_delete=models.PROTECT, related_name="postpaid_tenants",
    )
    enabled = models.BooleanField(default=False)
    suspended = models.BooleanField(default=False)
    sampled_at = models.DateTimeField(null=True, blank=True)
    monthly_rate = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    rate_duration_seconds = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "feature"], name="credits_unique_postpaid_feature",
            ),
        ]


class FeatureMeterPeriod(models.Model):
    company = models.ForeignKey(
        "companies.Company", on_delete=models.CASCADE, related_name="metered_feature_periods",
    )
    feature = models.ForeignKey("saas.PlanFeature", on_delete=models.PROTECT)
    period = models.CharField(max_length=7)
    accrued_credits = models.DecimalField(max_digits=22, decimal_places=8, default=Decimal("0"))
    activation_credits = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    covered_seconds = models.PositiveBigIntegerField(default=0)
    settled_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "feature", "period"], name="credits_unique_feature_meter_month",
            ),
        ]


class FeatureMeterReading(models.Model):
    company = models.ForeignKey(
        "companies.Company", on_delete=models.CASCADE, related_name="feature_meter_readings",
    )
    feature = models.ForeignKey("saas.PlanFeature", on_delete=models.PROTECT)
    sampled_at = models.DateTimeField()
    users = models.PositiveIntegerField(default=0)
    staff = models.PositiveIntegerField(default=0)
    monthly_rate = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    covered_seconds = models.PositiveIntegerField(default=0)
    accrued_credits = models.DecimalField(max_digits=22, decimal_places=8, default=Decimal("0"))

    class Meta:
        ordering = ["-sampled_at", "-pk"]
        indexes = [
            models.Index(fields=["company", "sampled_at"], name="feature_read_company_dt"),
        ]


class BillingEmailNotice(models.Model):
    """Durable, deduplicated monthly credit billing email notification.

    One persisted notice per tenant/admin/billing event. This is a delivery
    outbox, never a source of truth for balances, prices or entitlements.
    """
    reference = models.CharField(max_length=180, unique=True)
    company = models.ForeignKey(
        "companies.Company", on_delete=models.CASCADE, related_name="billing_email_notices",
    )
    recipient = models.EmailField()
    subject = models.CharField(max_length=180)
    message = models.TextField()
    status = models.CharField(
        max_length=12,
        choices=[("pending", "Pending"), ("failed", "Retry"), ("sent", "Sent")],
        default="pending",
        db_index=True,
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now, db_index=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=120, blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at", "pk"]
        indexes = [
            models.Index(fields=["status", "next_attempt_at"], name="billing_email_due"),
        ]


class ModulePriceLabel(models.Model):
    """A platform-managed single-module price uses the existing paid-label engine."""
    monthly_postpaid = models.BooleanField(default=False)
    feature = models.OneToOneField("saas.PlanFeature", on_delete=models.PROTECT, related_name="module_price_label")
    bundle = models.OneToOneField(FeatureBundle, on_delete=models.PROTECT, related_name="module_price")
    updated_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL)
    updated_at = models.DateTimeField(auto_now=True)


class MonthlyBillingPolicy(models.Model):
    """Legacy monthly collection policy; retained for migration/runtime compatibility."""
    enabled = models.BooleanField(default=False)
    due_day = models.PositiveSmallIntegerField(default=5)
    lock_after_due = models.BooleanField(default=True)
    feature_meter_interval_seconds = models.PositiveIntegerField(default=300)
    updated_at = models.DateTimeField(auto_now=True)


class MonthlyBillingCycle(models.Model):
    """Immutable due date for a legacy monthly collection period."""
    period = models.CharField(max_length=7, unique=True)
    due_at = models.DateTimeField()
    lock_after_due = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)


class BillingCyclePolicy(models.Model):
    """Single global, opt-in calendar-month postpaid settlement policy."""
    singleton = models.PositiveSmallIntegerField(unique=True, default=1, editable=False)
    due_time = models.TimeField(default=time(0, 5))
    calculation_interval_seconds = models.PositiveIntegerField(default=300)
    storage_tracking_interval_seconds = models.PositiveIntegerField(default=300)
    rate_duration_seconds = models.PositiveIntegerField(default=0)
    event_tracking_enabled = models.BooleanField(default=False)

    enabled = models.BooleanField(default=False)
    due_day = models.PositiveSmallIntegerField(default=5)
    restrict_overdue = models.BooleanField(default=True)
    allow_zero_balance_usage = models.BooleanField(default=True)
    # Prevent charging periods that closed before policy activation.
    effective_month = models.CharField(max_length=7, blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)


class BillingCycleStatement(models.Model):
    """Immutable invoice snapshot once a period has been finalized."""
    company = models.ForeignKey(
        "companies.Company", on_delete=models.CASCADE,
        related_name="calendar_billing_statements",
    )
    month = models.CharField(max_length=7)
    due_at = models.DateTimeField()
    feature_credits = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal("0"))
    storage_credits = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal("0"))
    total_credits = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal("0"))
    details = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=12, choices=[("pending", "Pending"), ("insufficient", "Insufficient credits"), ("paid", "Paid")],
        default="pending",
    )
    finalized_at = models.DateTimeField(default=timezone.now)
    settled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=["company", "month"], name="credits_one_calendar_statement_per_month",
        )]
        ordering = ["-month"]


class CompanyStorageState(models.Model):
    """Transactional byte counter; initialized once from existing MediaFile metadata."""
    company = models.OneToOneField("companies.Company", on_delete=models.CASCADE)
    used_bytes = models.PositiveBigIntegerField(default=0)
    file_count = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)
