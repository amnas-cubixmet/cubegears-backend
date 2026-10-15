from decimal import Decimal

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("credits", "0006_storage_monthly_metering"),
        ("saas", "0002_dynamic_plans"),
    ]

    operations = [
        migrations.CreateModel(
            name="CompanyPostpaidFeature",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("enabled", models.BooleanField(default=False)),
                ("suspended", models.BooleanField(default=False)),
                ("sampled_at", models.DateTimeField(blank=True, null=True)),
                ("monthly_rate", models.DecimalField(decimal_places=2, default=Decimal("0"), max_digits=14)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="postpaid_features", to="companies.company")),
                ("feature", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="postpaid_tenants", to="saas.planfeature")),
            ],
            options={"constraints": [models.UniqueConstraint(fields=("company", "feature"), name="credits_unique_postpaid_feature")]},
        ),
        migrations.CreateModel(
            name="FeatureMeterPeriod",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("period", models.CharField(max_length=7)),
                ("accrued_credits", models.DecimalField(decimal_places=8, default=Decimal("0"), max_digits=22)),
                ("activation_credits", models.DecimalField(decimal_places=2, default=Decimal("0"), max_digits=12)),
                ("covered_seconds", models.PositiveBigIntegerField(default=0)),
                ("settled_at", models.DateTimeField(blank=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="metered_feature_periods", to="companies.company")),
                ("feature", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="saas.planfeature")),
            ],
            options={"constraints": [models.UniqueConstraint(fields=("company", "feature", "period"), name="credits_unique_feature_meter_month")]},
        ),
        migrations.CreateModel(
            name="FeatureMeterReading",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("sampled_at", models.DateTimeField()),
                ("users", models.PositiveIntegerField(default=0)),
                ("staff", models.PositiveIntegerField(default=0)),
                ("monthly_rate", models.DecimalField(decimal_places=2, default=Decimal("0"), max_digits=14)),
                ("covered_seconds", models.PositiveIntegerField(default=0)),
                ("accrued_credits", models.DecimalField(decimal_places=8, default=Decimal("0"), max_digits=22)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="feature_meter_readings", to="companies.company")),
                ("feature", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="saas.planfeature")),
            ],
            options={
                "ordering": ["-sampled_at", "-pk"],
                "indexes": [models.Index(fields=["company", "sampled_at"], name="feature_read_company_dt")],
            },
        ),
    ]
