from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("credits", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="FeatureBundle",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.SlugField(max_length=100, unique=True)),
                ("name", models.CharField(max_length=120)),
                ("description", models.CharField(blank=True, max_length=255)),
                ("price_credits", models.DecimalField(decimal_places=2, max_digits=12)),
                ("period", models.CharField(choices=[
                    ("permanent", "Permanent"), ("weekly", "Weekly"),
                    ("daily", "Daily"), ("hourly", "Hourly"), ("per_use", "Per use"),
                ], max_length=12)),
                ("is_active", models.BooleanField(default=True)),
                ("sort_order", models.PositiveIntegerField(default=0)),
                ("features", models.ManyToManyField(related_name="credit_bundles", to="saas.planfeature")),
            ],
            options={"ordering": ["sort_order", "name"]},
        ),
        migrations.CreateModel(
            name="CompanyFeatureBundle",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("enabled", models.BooleanField(default=True)),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("remaining_uses", models.PositiveIntegerField(blank=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("bundle", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="company_access", to="credits.featurebundle")),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="feature_bundles", to="companies.company")),
            ],
        ),
        migrations.AddConstraint(
            model_name="companyfeaturebundle",
            constraint=models.UniqueConstraint(fields=("company", "bundle"), name="credits_unique_company_bundle"),
        ),
    ]
