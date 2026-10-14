from decimal import Decimal
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("saas", "0001_initial"), ("companies", "0001_initial"), ("accounts", "0001_initial")]

    operations = [
        migrations.CreateModel(
            name="FeatureGroup",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.SlugField(max_length=100, unique=True)),
                ("label", models.CharField(max_length=120)),
                ("sort_order", models.PositiveIntegerField(default=0)),
                ("is_active", models.BooleanField(default=True)),
                ("parent", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="children", to="saas.featuregroup")),
            ],
            options={"ordering": ["sort_order", "label"]},
        ),
        migrations.CreateModel(
            name="PlanFeature",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.SlugField(max_length=100, unique=True)),
                ("label", models.CharField(max_length=160)),
                ("description", models.CharField(blank=True, max_length=255)),
                ("sort_order", models.PositiveIntegerField(default=0)),
                ("is_active", models.BooleanField(default=True)),
                ("group", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="features", to="saas.featuregroup")),
            ],
            options={"ordering": ["sort_order", "label"]},
        ),
        migrations.CreateModel(
            name="SubscriptionPlan",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.SlugField(max_length=80, unique=True)),
                ("name", models.CharField(max_length=120)),
                ("description", models.CharField(blank=True, max_length=400)),
                ("monthly_price", models.DecimalField(decimal_places=2, default=Decimal("0"), max_digits=12)),
                ("yearly_price", models.DecimalField(decimal_places=2, default=Decimal("0"), max_digits=12)),
                ("promo_percent", models.PositiveSmallIntegerField(default=0)),
                ("promo_ends_at", models.DateTimeField(blank=True, null=True)),
                ("max_branches", models.PositiveIntegerField(default=1)),
                ("max_users", models.PositiveIntegerField(default=3)),
                ("included_storage_gb", models.PositiveIntegerField(default=5)),
                ("extra_storage_rate", models.DecimalField(decimal_places=2, default=Decimal("15"), max_digits=10)),
                ("trial_days", models.PositiveIntegerField(default=14)),
                ("is_public", models.BooleanField(default=True)),
                ("is_active", models.BooleanField(default=True)),
                ("sort_order", models.PositiveIntegerField(default=0)),
            ],
            options={"ordering": ["sort_order", "monthly_price", "name"]},
        ),
        migrations.CreateModel(
            name="PlanFeatureAccess",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("enabled", models.BooleanField(default=False)),
                ("feature", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="plan_access", to="saas.planfeature")),
                ("plan", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="feature_access", to="saas.subscriptionplan")),
            ],
        ),
        migrations.AddField(
            model_name="subscriptionplan",
            name="features",
            field=models.ManyToManyField(related_name="plans", through="saas.PlanFeatureAccess", to="saas.planfeature"),
        ),
        migrations.AddConstraint(
            model_name="planfeatureaccess",
            constraint=models.UniqueConstraint(fields=("plan", "feature"), name="unique_plan_feature_access"),
        ),
        migrations.AddField(
            model_name="subscription",
            name="plan_definition",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="subscriptions", to="saas.subscriptionplan"),
        ),
        migrations.CreateModel(
            name="PlanChangeRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("billing_cycle", models.CharField(choices=[("monthly", "Monthly"), ("yearly", "Yearly")], max_length=10)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("contacted", "Contacted"), ("approved", "Approved"), ("rejected", "Rejected")], default="pending", max_length=20)),
                ("message", models.TextField(blank=True, max_length=2000)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="plan_change_requests", to="companies.company")),
                ("requested_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to="accounts.user")),
                ("requested_plan", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="saas.subscriptionplan")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="CustomPlanRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("email", models.EmailField(max_length=254)),
                ("message", models.TextField(max_length=4000)),
                ("branches", models.PositiveIntegerField(default=1)),
                ("users", models.PositiveIntegerField(default=1)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("contacted", "Contacted"), ("approved", "Approved"), ("rejected", "Rejected")], default="pending", max_length=20)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="custom_plan_requests", to="companies.company")),
                ("requested_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to="accounts.user")),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
