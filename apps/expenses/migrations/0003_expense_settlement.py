"""Audited expense settlements; existing expense rows are not backfilled."""
import uuid
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("expenses", "0002_expense_account"),
        ("inventory", "0008_workshopfinancialaccount_account_number_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="expense",
            name="status",
            field=models.CharField(max_length=30, default="Unpaid"),
        ),
        migrations.CreateModel(
            name="ExpenseSettlement",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("amount", models.DecimalField(max_digits=14, decimal_places=2)),
                ("method", models.CharField(max_length=50, default="Cash")),
                ("reference", models.CharField(max_length=120, blank=True)),
                ("idempotency_key", models.CharField(max_length=128)),
                ("posted_at", models.DateTimeField()),
                ("reversed_at", models.DateTimeField(blank=True, null=True)),
                ("reversal_reason", models.TextField(blank=True)),
                ("account", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="expense_settlements", to="inventory.workshopfinancialaccount")),
                ("expense", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="settlements", to="expenses.expense")),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="companies.company")),
                ("branch", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="branches.branch")),
                ("recorded_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ("reversed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="reversed_expense_settlements", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-posted_at", "-created_at"],
                "constraints": [
                    models.UniqueConstraint(fields=("company", "idempotency_key"), name="unique_expense_settlement_request"),
                ],
            },
        ),
    ]
