from decimal import Decimal
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [("credits", "0005_bundle_monthly_seat_pricing")]

    operations = [
        migrations.AddField(
            model_name="storagecreditpolicy", name="billing_mode",
            field=models.CharField(max_length=16, default="snapshot",
                choices=[
                    ("snapshot", "Period snapshot (prepaid)"),
                    ("metered_monthly", "Metered usage / monthly settlement"),
                ]),
        ),
        migrations.AddField(
            model_name="storagecreditpolicy", name="meter_interval_seconds",
            field=models.PositiveIntegerField(default=3600),
        ),
        migrations.CreateModel(
            name="FeatureUsageSession",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("started_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("ended_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("monthly_credits", models.DecimalField(decimal_places=2, max_digits=16)),
                ("activation_credits", models.DecimalField(decimal_places=2, max_digits=12, default=Decimal("0.00"))),
                ("user_seats", models.PositiveIntegerField(default=0)),
                ("staff_seats", models.PositiveIntegerField(default=0)),
                ("company", models.ForeignKey(to="companies.company", on_delete=django.db.models.deletion.CASCADE, related_name="feature_usage_sessions")),
                ("feature", models.ForeignKey(to="saas.planfeature", on_delete=django.db.models.deletion.PROTECT, related_name="postpaid_sessions")),
            ],
            options={"constraints": [models.UniqueConstraint(fields=["company", "feature"], condition=models.Q(ended_at__isnull=True), name="credits_one_open_feature_usage")]},
        ),
        migrations.CreateModel(
            name="StorageUsageInterval",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("starts_at", models.DateTimeField()),
                ("ends_at", models.DateTimeField()),
                ("billing_month", models.CharField(db_index=True, max_length=7)),
                ("measured_bytes", models.PositiveBigIntegerField()),
                ("credits_accrued", models.DecimalField(decimal_places=6, max_digits=18)),
                ("rate_per_gb_month", models.DecimalField(decimal_places=2, max_digits=12)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("company", models.ForeignKey(to="companies.company", on_delete=django.db.models.deletion.CASCADE, related_name="storage_meter_intervals")),
            ],
            options={
                "constraints": [models.UniqueConstraint(fields=["company", "ends_at"], name="credits_unique_company_storage_tick")],
                "indexes": [models.Index(fields=["company", "billing_month"], name="credit_storage_company_mo")],
            },
        ),
        migrations.CreateModel(
            name="MonthlyUsageInvoice",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("category", models.CharField(max_length=12, choices=[("feature", "Feature"), ("storage", "Storage")])),
                ("billing_month", models.CharField(max_length=7)),
                ("credits_due", models.DecimalField(decimal_places=2, max_digits=16, default=Decimal("0.00"))),
                ("status", models.CharField(max_length=14, choices=[("pending", "Pending"), ("paid", "Paid"), ("insufficient", "Insufficient credits")], default="pending")),
                ("details", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("settled_at", models.DateTimeField(blank=True, null=True)),
                ("company", models.ForeignKey(to="companies.company", on_delete=django.db.models.deletion.CASCADE, related_name="monthly_usage_invoices")),
            ],
            options={"constraints": [models.UniqueConstraint(fields=["company", "category", "billing_month"], name="credits_unique_company_monthly_usage")]},
        ),
    ]
