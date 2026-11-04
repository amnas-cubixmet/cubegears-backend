import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models
import apps.vehicles.models


class Migration(migrations.Migration):
    dependencies = [
        ("vehicles", "0001_initial"),
        ("jobs", "0007_outside_part_bill"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="VehicleInspectionPhoto",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("file", models.ImageField(storage=apps.vehicles.models.vehicle_photo_private_storage,
                                           upload_to=apps.vehicles.models.vehicle_photo_upload_to, max_length=255)),
                ("category", models.CharField(max_length=24, default="additional", choices=[
                    ("front","Front View"),("rear","Rear View"),("left","Left Side"),
                    ("right","Right Side"),("interior","Interior"),("dashboard","Dashboard"),
                    ("engine","Engine Bay"),("odometer","Odometer"),("damage","Existing Damage"),
                    ("additional","Additional Photo"),
                ])),
                ("phase", models.CharField(max_length=12, default="before", choices=[
                    ("before","Before Service"),("progress","Work Progress"),("after","After Service"),
                ])),
                ("caption", models.CharField(blank=True, max_length=255)),
                ("notes", models.TextField(blank=True)),
                ("published", models.BooleanField(default=False)),
                ("captured_at", models.DateTimeField(auto_now_add=True)),
                ("branch", models.ForeignKey(blank=True, null=True, to="branches.branch", on_delete=django.db.models.deletion.SET_NULL)),
                ("company", models.ForeignKey(to="companies.company", on_delete=django.db.models.deletion.CASCADE)),
                ("vehicle", models.ForeignKey(to="vehicles.vehicle", related_name="inspection_photos", on_delete=django.db.models.deletion.CASCADE)),
                ("job", models.ForeignKey(blank=True, null=True, to="jobs.job", related_name="vehicle_inspection_photos", on_delete=django.db.models.deletion.SET_NULL)),
                ("captured_by", models.ForeignKey(blank=True, null=True, to=settings.AUTH_USER_MODEL, related_name="vehicle_photos_captured", on_delete=django.db.models.deletion.SET_NULL)),
            ],
            options={"ordering":["-captured_at"]},
        ),
        migrations.AddIndex(
            model_name="vehicleinspectionphoto",
            index=models.Index(fields=["company","vehicle","published"], name="vehicle_photo_visibility_idx"),
        ),
    ]
