import uuid
from pathlib import Path
from django.db import models


def company_logo_upload_to(instance, filename):
    # Avoid trusting browser-provided paths/names; keep uploads tenant-scoped.
    extension = Path(filename).suffix.lower()
    if extension == ".jpeg":
        extension = ".jpg"
    if extension not in {".jpg", ".png", ".webp"}:
        extension = ".png"
    return f"company_logos/{instance.pk}/{uuid.uuid4().hex}{extension}"

class Company(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=180)
    legal_name = models.CharField(max_length=220, blank=True)
    slug = models.SlugField(max_length=180, unique=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    gstin = models.CharField(max_length=20, blank=True)
    address = models.TextField(blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, default="India")
    pincode = models.CharField(max_length=12, blank=True)
    currency = models.CharField(max_length=8, default="INR")
    logo = models.URLField(blank=True)  # Legacy external logo URL, retained for existing records.
    logo_image = models.ImageField(upload_to=company_logo_upload_to, blank=True)
    owner = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="owned_companies",
    )
    # Legacy companies keep their existing rollout; public signup always locks modules.
    require_paid_features = models.BooleanField(default=False)
    # Workshop safety policy: skips are prohibited while this is enabled.
    require_vehicle_inspection = models.BooleanField(default=False)
    require_customer_estimate_approval = models.BooleanField(default=False)
    plan = models.CharField(max_length=50, default="starter")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class CompanyWebsiteSettings(models.Model):
    id = models.BigAutoField(primary_key=True)
    """Optional customer-facing appearance; legacy Company remains authoritative."""
    company = models.OneToOneField(
        Company, on_delete=models.CASCADE, related_name="website_settings"
    )
    public_name = models.CharField(max_length=180, blank=True)
    description = models.CharField(max_length=500, blank=True)
    welcome_message = models.CharField(max_length=240, blank=True)
    contact_phone = models.CharField(max_length=30, blank=True)
    whatsapp_phone = models.CharField(max_length=30, blank=True)
    primary_color = models.CharField(max_length=7, default="#116f69")
    secondary_color = models.CharField(max_length=7, default="#e4f2ec")
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Customer website for {self.company.name}"


class CompanyDomain(models.Model):
    id = models.BigAutoField(primary_key=True)
    """Custom domains are NEVER active on user submission alone."""
    PENDING_SETUP = "pending_setup"
    PENDING_DNS = "pending_dns"
    PENDING_VERIFICATION = "pending_verification"
    SSL_PENDING = "ssl_pending"
    ACTIVE = "active"
    FAILED = "failed"
    DISABLED = "disabled"
    STATUS_CHOICES = [
        (PENDING_SETUP, "Pending Setup"),
        (PENDING_DNS, "Pending DNS"),
        (PENDING_VERIFICATION, "Pending Verification"),
        (SSL_PENDING, "SSL Pending"),
        (ACTIVE, "Active"),
        (FAILED, "Failed"),
        (DISABLED, "Disabled"),
    ]
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="customer_domains"
    )
    hostname = models.CharField(max_length=253, unique=True, db_index=True)
    status = models.CharField(
        max_length=30, choices=STATUS_CHOICES, default=PENDING_DNS
    )
    is_primary = models.BooleanField(default=False)
    verified_at = models.DateTimeField(null=True, blank=True)
    provider_reference = models.CharField(max_length=180, blank=True)
    dns_provider = models.CharField(max_length=24, default="other", choices=[
        ("godaddy", "GoDaddy"), ("cloudflare", "Cloudflare"),
        ("hostinger", "Hostinger"), ("namecheap", "Namecheap"),
        ("vercel", "Vercel DNS"), ("other", "Other"),
    ])
    dns_records = models.JSONField(default=list, blank=True)
    verification_records = models.JSONField(default=list, blank=True)
    dns_configured = models.BooleanField(default=False)
    ownership_verified = models.BooleanField(default=False)
    ssl_status = models.CharField(max_length=16, default="pending")
    last_error = models.CharField(max_length=500, blank=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    # Platform may require manual approval before exposing any customer data.
    is_approved = models.BooleanField(default=True)
    approved_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="approved_customer_domains",
    )
    vercel_project_id = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company"], condition=models.Q(is_primary=True),
                name="one_primary_website_domain_per_company",
            ),
        ]

    def __str__(self):
        return self.hostname


class CompanyDomainAudit(models.Model):
    id = models.BigAutoField(primary_key=True)
    domain = models.ForeignKey(CompanyDomain, on_delete=models.CASCADE, related_name="events")
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    actor = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=40)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

