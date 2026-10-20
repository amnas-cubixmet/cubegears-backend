from django.db import migrations, models


FEATURE_PERIODS = [
    ("ten_seconds", "10 Seconds"),
    ("hourly", "Hourly"),
    ("daily", "Daily"),
    ("weekly", "Weekly"),
    ("monthly", "30 days"),
    ("permanent", "Permanent"),
]
BUNDLE_PERIODS = [
    ("ten_seconds", "10 Seconds"),
    ("hourly", "Hourly (1 hour)"),
    ("daily", "Daily (24 hours)"),
    ("weekly", "Weekly (7 days)"),
    ("monthly", "Monthly (30 days)"),
    ("permanent", "Permanent"),
    ("per_use", "Per use (one prepaid operation)"),
]


class Migration(migrations.Migration):
    dependencies = [("credits", "0022_storage_monitoring_readings")]

    operations = [
        migrations.AlterField(
            model_name="featurecreditprice",
            name="billing_period",
            field=models.CharField(max_length=15, default="monthly", choices=FEATURE_PERIODS),
        ),
        migrations.AddField(
            model_name="companypostpaidfeature",
            name="billing_period",
            field=models.CharField(max_length=15, default="monthly", choices=BUNDLE_PERIODS),
        ),
        migrations.AddField(
            model_name="featuremeterreading",
            name="billing_period",
            field=models.CharField(max_length=15, default="monthly", choices=BUNDLE_PERIODS),
        ),
        migrations.AddField(
            model_name="featuremeterreading",
            name="rate_duration_seconds",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
