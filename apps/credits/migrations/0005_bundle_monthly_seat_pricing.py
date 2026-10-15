
from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("credits", "0004_metered_feature_and_storage_rates")]

    operations = [
        migrations.AddField(
            model_name="featurebundle", name="activation_credits",
            field=models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00")),
        ),
        migrations.AddField(
            model_name="featurebundle", name="per_user_credits",
            field=models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00")),
        ),
        migrations.AlterField(
            model_name="featurebundle", name="billing_type",
            field=models.CharField(
                choices=[
                    ("permanent", "Permanent"), ("weekly", "Weekly (7 days)"),
                    ("monthly", "Monthly (30 days)"), ("daily", "Daily (24 hours)"),
                    ("hourly", "Hourly (1 hour)"), ("per_use", "Per use (one prepaid operation)"),
                ],
                max_length=15, default="weekly",
            ),
        ),
        migrations.AlterField(
            model_name="companybundleaccess", name="billing_type_at_purchase",
            field=models.CharField(
                choices=[
                    ("permanent", "Permanent"), ("weekly", "Weekly (7 days)"),
                    ("monthly", "Monthly (30 days)"), ("daily", "Daily (24 hours)"),
                    ("hourly", "Hourly (1 hour)"), ("per_use", "Per use (one prepaid operation)"),
                ],
                max_length=15, default="weekly",
            ),
        ),
    ]
