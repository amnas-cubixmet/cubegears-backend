from django.db import models
from common.models import CompanyOwnedModel

class Vehicle(CompanyOwnedModel):
    customer=models.ForeignKey("customers.Customer",on_delete=models.CASCADE,related_name="vehicles")
    registration=models.CharField(max_length=30)
    make=models.CharField(max_length=80,blank=True)
    model=models.CharField(max_length=80,blank=True)
    variant=models.CharField(max_length=80,blank=True)
    year=models.PositiveIntegerField(null=True,blank=True)
    fuel_type=models.CharField(max_length=30,blank=True)
    transmission=models.CharField(max_length=30,blank=True)
    color=models.CharField(max_length=40,blank=True)
    odometer=models.PositiveIntegerField(default=0)
    vin=models.CharField(max_length=64,blank=True)
    engine_no=models.CharField(max_length=64,blank=True)
    last_service_date=models.DateField(null=True,blank=True)
    next_service_due=models.DateField(null=True,blank=True)
    insurance_expiry=models.DateField(null=True,blank=True)
    notes=models.TextField(blank=True)
    status=models.CharField(max_length=20,default="Active")

    class Meta:
        ordering=["registration"]
        constraints=[models.UniqueConstraint(fields=["company","registration"],name="unique_vehicle_registration_per_company")]

    def __str__(self): return self.registration


# Vehicle inspection images are kept completely outside public MEDIA_ROOT.
# Both staff and customer requests must use validated streaming endpoints.
import uuid
from django.conf import settings
from django.core.files.storage import FileSystemStorage


def vehicle_photo_private_storage():
    return FileSystemStorage(
        location=getattr(settings, "PRIVATE_JOB_MEDIA_ROOT", settings.BASE_DIR / "private_job_media"),
        base_url=None,
    )


def vehicle_photo_upload_to(instance, filename):
    return f"vehicle-inspections/{instance.company_id}/{instance.vehicle_id}/{uuid.uuid4().hex}.jpg"


class VehicleInspectionPhoto(CompanyOwnedModel):
    CATEGORY_CHOICES = [
        (value, label) for value, label in [
            ("front", "Front View"), ("rear", "Rear View"),
            ("left", "Left Side"), ("right", "Right Side"),
            ("interior", "Interior"), ("dashboard", "Dashboard"),
            ("engine", "Engine Bay"), ("odometer", "Odometer"),
            ("damage", "Existing Damage"), ("additional", "Additional Photo"),
        ]
    ]
    PHASE_CHOICES = [
        ("before", "Before Service"), ("progress", "Work Progress"),
        ("after", "After Service"),
    ]
    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, related_name="inspection_photos")
    job = models.ForeignKey("jobs.Job", on_delete=models.SET_NULL, null=True, blank=True, related_name="vehicle_inspection_photos")
    file = models.ImageField(storage=vehicle_photo_private_storage, upload_to=vehicle_photo_upload_to, max_length=255)
    category = models.CharField(max_length=24, choices=CATEGORY_CHOICES, default="additional")
    phase = models.CharField(max_length=12, choices=PHASE_CHOICES, default="before")
    caption = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)
    published = models.BooleanField(default=False)
    submitted_by_customer = models.BooleanField(default=False, db_index=True)
    captured_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="vehicle_photos_captured")
    captured_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-captured_at"]
        indexes = [
            models.Index(fields=["company", "vehicle", "published"], name="vehicle_photo_visibility_idx"),
        ]
