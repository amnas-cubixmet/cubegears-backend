from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ("credits", "0021_featurebundle_is_retired_and_ten_seconds"),
    ]

    operations = [
        migrations.CreateModel(
            name="StorageMonitoringReading",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("sampled_at", models.DateTimeField()),
                ("interval_seconds", models.PositiveIntegerField()),
                ("bytes_used", models.PositiveBigIntegerField(default=0)),
                ("file_count", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="storage_monitoring_readings", to="companies.company")),
            ],
            options={"ordering": ["-sampled_at", "-pk"]},
        ),
        migrations.AddConstraint(
            model_name="storagemonitoringreading",
            constraint=models.UniqueConstraint(fields=("company", "sampled_at"), name="credits_monitor_company_tick"),
        ),
        migrations.AddIndex(
            model_name="storagemonitoringreading",
            index=models.Index(fields=["company", "sampled_at"], name="storage_mon_company_at"),
        ),
    ]
