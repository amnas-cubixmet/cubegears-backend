from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ("credits", "0008_merge_postpaid_metering"),
        ("credits", "0008_merge_postpaid_and_monthly_metering"),
        ("companies", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="BillingEmailNotice",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("reference", models.CharField(max_length=180, unique=True)),
                ("recipient", models.EmailField(max_length=254)),
                ("subject", models.CharField(max_length=180)),
                ("message", models.TextField()),
                ("status", models.CharField(max_length=12, choices=[("pending", "Pending"), ("failed", "Retry"), ("sent", "Sent")], default="pending", db_index=True)),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("next_attempt_at", models.DateTimeField(default=django.utils.timezone.now, db_index=True)),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.CharField(max_length=120, blank=True, default="")),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="billing_email_notices", to="companies.company")),
            ],
            options={
                "ordering": ["created_at", "pk"],
                "indexes": [models.Index(fields=["status", "next_attempt_at"], name="billing_email_due")],
            },
        ),
    ]
