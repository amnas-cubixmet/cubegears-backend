from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("credits", "0019_publish_billable_modules_five_credits"),
    ]

    operations = [
        migrations.CreateModel(
            name="StorageMonitoringPolicy",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("singleton", models.PositiveSmallIntegerField(default=1, editable=False, unique=True)),
                ("enabled", models.BooleanField(default=False)),
                ("interval_seconds", models.PositiveIntegerField(default=43200)),
                ("last_run_at", models.DateTimeField(blank=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
    ]
