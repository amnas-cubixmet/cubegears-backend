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



class CompanyWebsiteSubdomain(models.Model):
    """Dedicated public-site hostname; never mutate Company.slug (staff login ID).

    The unique label is a global namespace shared by all workshop customer
    websites under CUSTOMER_PORTAL_ROOT_DOMAIN. Backfilled for legacy tenants.
    """
    id = models.BigAutoField(primary_key=True)
    company = models.OneToOneField(
        Company, on_delete=models.CASCADE, related_name="website_subdomain"
    )
    label = models.SlugField(max_length=63, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.label
