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
    plan = models.CharField(max_length=50, default="starter")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name
