from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("credits", "0020_storage_monitoring_policy"),
    ]

    operations = [
        migrations.AddField(
            model_name="featurebundle",
            name="is_retired",
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name="featurebundle",
            name="billing_type",
            field=models.CharField(
                max_length=15,
                default="weekly",
                choices=[
                    ("permanent", "Permanent"),
                    ("ten_seconds", "10 Seconds"),
                    ("weekly", "Weekly (7 days)"),
                    ("monthly", "Monthly (30 days)"),
                    ("daily", "Daily (24 hours)"),
                    ("hourly", "Hourly (1 hour)"),
                    ("per_use", "Per use (one prepaid operation)"),
                ],
            ),
        ),
        migrations.AlterField(
            model_name="companybundleaccess",
            name="billing_type_at_purchase",
            field=models.CharField(
                max_length=15,
                default="weekly",
                choices=[
                    ("permanent", "Permanent"),
                    ("ten_seconds", "10 Seconds"),
                    ("weekly", "Weekly (7 days)"),
                    ("monthly", "Monthly (30 days)"),
                    ("daily", "Daily (24 hours)"),
                    ("hourly", "Hourly (1 hour)"),
                    ("per_use", "Per use (one prepaid operation)"),
                ],
            ),
        ),
    ]
