from decimal import Decimal

from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ("credits", "0001_initial"),
        ("saas", "0007_merge_legacy_payment_branch"),
    ]

    operations = [
        migrations.CreateModel(
            name="FeatureBundle",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.SlugField(max_length=100, unique=True)),
                ("name", models.CharField(max_length=120)),
                ("description", models.CharField(blank=True, max_length=400)),
                ("price_credits", models.DecimalField(decimal_places=2, max_digits=12)),
                ("billing_type", models.CharField(choices=[
                    ("permanent", "Permanent"), ("weekly", "Weekly (7 days)"),
                    ("daily", "Daily (24 hours)"), ("hourly", "Hourly (1 hour)"),
                    ("per_use", "Per use (one prepaid operation)"),
                ], default="weekly", max_length=15)),
                ("is_active", models.BooleanField(default=True)),
                ("sort_order", models.PositiveIntegerField(default=0)),
                ("features", models.ManyToManyField(related_name="credit_bundles", to="saas.planfeature")),
            ],
            options={"ordering": ["sort_order", "name"]},
        ),
        migrations.CreateModel(
            name="CompanyBundleAccess",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("enabled", models.BooleanField(default=True)),
                ("valid_until", models.DateTimeField(blank=True, null=True)),
                ("uses_remaining", models.PositiveIntegerField(default=0)),
                ("last_price_credits", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=12)),
                ("purchased_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("bundle", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="company_access", to="credits.featurebundle")),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="bundle_access", to="companies.company")),
            ],
        ),
        migrations.AddConstraint(
            model_name="companybundleaccess",
            constraint=models.UniqueConstraint(
                fields=("company", "bundle"), name="credits_unique_company_bundle"
            ),
        ),
    ]
