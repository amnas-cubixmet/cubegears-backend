from decimal import Decimal

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("credits", "0005_bundle_monthly_seat_pricing"),
        ("companies", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="StorageMeterCursor",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("sampled_at", models.DateTimeField(blank=True, null=True)),
                ("used_bytes", models.BigIntegerField(default=0)),
                ("billable_gb", models.DecimalField(decimal_places=6, default=Decimal("0"), max_digits=18)),
                ("rate_per_gb", models.DecimalField(decimal_places=2, default=Decimal("0"), max_digits=12)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("company", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="storage_meter_cursor", to="companies.company")),
            ],
        ),
        migrations.CreateModel(
            name="StorageMeterPeriod",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("period", models.CharField(max_length=7)),
                ("accrued_credits", models.DecimalField(decimal_places=8, default=Decimal("0"), max_digits=22)),
                ("covered_seconds", models.PositiveBigIntegerField(default=0)),
                ("settled_at", models.DateTimeField(blank=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="metered_storage_periods", to="companies.company")),
            ],
            options={"constraints": [models.UniqueConstraint(fields=("company", "period"), name="storage_meter_unique_month")]},
        ),
        migrations.CreateModel(
            name="StorageMeterReading",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("sampled_at", models.DateTimeField()),
                ("used_bytes", models.BigIntegerField(default=0)),
                ("billable_gb", models.DecimalField(decimal_places=6, default=Decimal("0"), max_digits=18)),
                ("rate_per_gb", models.DecimalField(decimal_places=2, default=Decimal("0"), max_digits=12)),
                ("covered_seconds", models.PositiveIntegerField(default=0)),
                ("accrued_credits", models.DecimalField(decimal_places=8, default=Decimal("0"), max_digits=22)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="storage_meter_readings", to="companies.company")),
            ],
            options={
                "ordering": ["-sampled_at", "-pk"],
                "indexes": [models.Index(fields=["company", "sampled_at"], name="storage_read_company_dt")],
            },
        ),
    ]
