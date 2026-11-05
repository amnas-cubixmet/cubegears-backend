import uuid
from django.conf import settings

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0003_stockmovement_unit_selling_price"),
        ("expenses", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="SupplierCredit",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("due_date", models.DateField(blank=True, null=True)),
                ("branch", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="branches.branch")),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="companies.company")),
                ("purchase_order", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="supplier_credit", to="inventory.purchaseorder")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="SupplierCreditPayment",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("idempotency_key", models.UUIDField()),
                ("branch", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="branches.branch")),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="companies.company")),
                ("credit", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="payments", to="inventory.suppliercredit")),
                ("expense", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="supplier_credit_allocation", to="expenses.expense")),
                ("recorded_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="accounts.user")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddConstraint(
            model_name="suppliercreditpayment",
            constraint=models.UniqueConstraint(fields=("company", "idempotency_key"), name="unique_supplier_payment_request_per_company"),
        ),
    ]
