"""Persist reusable workshop-specific vehicle makes and models."""

import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("vehicles", "0002_vehicle_inspection_photo"),
    ]

    operations = [
        migrations.CreateModel(
            name="VehicleCatalogEntry",
            fields=[
                ("id", models.UUIDField(
                    default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                )),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("make", models.CharField(max_length=80)),
                ("model", models.CharField(max_length=80, blank=True)),
                ("make_key", models.CharField(max_length=80, editable=False)),
                ("model_key", models.CharField(max_length=80, blank=True, editable=False)),
                ("branch", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    to="branches.branch",
                )),
                ("company", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE, to="companies.company"
                )),
            ],
            options={
                "ordering": ["make_key", "model_key"],
            },
        ),
        migrations.AddConstraint(
            model_name="vehiclecatalogentry",
            constraint=models.UniqueConstraint(
                fields=("company", "make_key", "model_key"),
                name="unique_vehicle_catalog_entry_per_company",
            ),
        ),
    ]
