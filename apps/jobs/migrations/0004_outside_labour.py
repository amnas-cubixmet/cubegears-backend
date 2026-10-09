import uuid
from decimal import Decimal

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("jobs", "0003_customer_feedback"),
        ("expenses", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="OutsideLabourCharge",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("worker_name", models.CharField(max_length=160)),
                ("worker_phone", models.CharField(max_length=30, blank=True)),
                ("work_description", models.CharField(max_length=300)),
                ("customer_charge", models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))),
                ("worker_charge", models.DecimalField(max_digits=12, decimal_places=2)),
                ("status", models.CharField(choices=[("Pending", "Pending"), ("Paid", "Paid")], default="Pending", max_length=12)),
                ("payment_method", models.CharField(blank=True, max_length=40)),
                ("payment_reference", models.CharField(blank=True, max_length=120)),
                ("paid_at", models.DateTimeField(blank=True, null=True)),
                ("branch", models.ForeignKey(to="branches.branch", on_delete=django.db.models.deletion.SET_NULL, null=True, blank=True)),
                ("company", models.ForeignKey(to="companies.company", on_delete=django.db.models.deletion.CASCADE)),
                ("job", models.ForeignKey(to="jobs.job", on_delete=django.db.models.deletion.PROTECT, related_name="outside_labour")),
                ("created_by", models.ForeignKey(to=settings.AUTH_USER_MODEL, on_delete=django.db.models.deletion.SET_NULL, blank=True, null=True, related_name="created_outside_labour")),
                ("paid_by", models.ForeignKey(to=settings.AUTH_USER_MODEL, on_delete=django.db.models.deletion.SET_NULL, blank=True, null=True, related_name="paid_outside_labour")),
                ("expense", models.OneToOneField(to="expenses.expense", on_delete=django.db.models.deletion.PROTECT, blank=True, null=True, related_name="outside_labour")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(
            model_name="outsidelabourcharge",
            index=models.Index(fields=["company", "status"], name="outlab_company_status_idx"),
        ),
    ]
