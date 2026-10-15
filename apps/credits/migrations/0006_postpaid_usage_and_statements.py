from decimal import Decimal

from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ("credits", "0005_bundle_monthly_seat_pricing"),
        ("companies", "0001_initial"),
        ("saas", "0002_dynamic_plans"),
    ]

    operations = [
        migrations.CreateModel(
            name="PostpaidFeatureState",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("enabled", models.BooleanField(default=False)),
                ("meter_started_at", models.DateTimeField(blank=True, null=True)),
                ("activated_at", models.DateTimeField(blank=True, null=True)),
                ("base_credits", models.DecimalField(decimal_places=2, max_digits=12, default=Decimal("0"))),
                ("per_user_credits", models.DecimalField(decimal_places=2, max_digits=12, default=Decimal("0"))),
                ("per_staff_credits", models.DecimalField(decimal_places=2, max_digits=12, default=Decimal("0"))),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="postpaid_feature_states", to="companies.company")),
                ("feature", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="postpaid_states", to="saas.planfeature")),
            ],
            options={"constraints": [models.UniqueConstraint(fields=("company", "feature"), name="credit_postpaid_unique_company_feature")]},
        ),
        migrations.CreateModel(
            name="PostpaidUsageRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("kind", models.CharField(choices=[("feature_usage", "Feature usage"), ("feature_activation", "Feature activation")], max_length=32)),
                ("period", models.CharField(db_index=True, max_length=7)),
                ("from_at", models.DateTimeField()),
                ("until_at", models.DateTimeField()),
                ("user_count", models.PositiveIntegerField(default=0)),
                ("staff_count", models.PositiveIntegerField(default=0)),
                ("credits", models.DecimalField(decimal_places=8, max_digits=18, default=Decimal("0"))),
                ("reference", models.CharField(max_length=180, unique=True)),
                ("recorded_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="postpaid_usage", to="companies.company")),
                ("feature", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to="saas.planfeature")),
            ],
            options={"ordering": ["from_at", "id"], "indexes": [models.Index(fields=["company", "period", "kind"], name="credit_postpaid_usage_period")]},
        ),
        migrations.CreateModel(
            name="StorageMeterSample",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("sampled_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("bytes_used", models.PositiveBigIntegerField(default=0)),
                ("file_count", models.PositiveIntegerField(default=0)),
                ("reference", models.CharField(max_length=180, unique=True)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="storage_meter_samples", to="companies.company")),
            ],
            options={"ordering": ["-sampled_at"], "indexes": [models.Index(fields=["company", "sampled_at"], name="credit_storage_sample_dt")]},
        ),
        migrations.CreateModel(
            name="MonthlyCreditStatement",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("period", models.CharField(max_length=7)),
                ("feature_credits", models.DecimalField(decimal_places=2, max_digits=16, default=Decimal("0"))),
                ("storage_credits", models.DecimalField(decimal_places=2, max_digits=16, default=Decimal("0"))),
                ("total_credits", models.DecimalField(decimal_places=2, max_digits=16, default=Decimal("0"))),
                ("details", models.JSONField(default=dict)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("paid", "Paid")], default="pending", max_length=16)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("paid_at", models.DateTimeField(blank=True, null=True)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="monthly_credit_statements", to="companies.company")),
            ],
            options={"ordering": ["-period", "-id"], "constraints": [models.UniqueConstraint(fields=("company", "period"), name="credit_monthly_company_period")]},
        ),
    ]
