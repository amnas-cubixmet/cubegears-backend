"""Supplier returns, debit note credits and refunds; no legacy record mutations."""
from uuid import uuid4

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0011_stock_category_hierarchy"),
    ]

    operations = [
        migrations.AlterField(
            model_name="stockmovement",
            name="movement_type",
            field=models.CharField(
                max_length=30,
                choices=[(name, name) for name in [
                    "purchase", "job_issue", "sale", "return", "transfer_in",
                    "transfer_out", "adjustment", "opening", "purchase_return",
                ]],
            ),
        ),
        migrations.CreateModel(
            name="SupplierDebitNote",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid4, editable=False, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("number", models.CharField(max_length=100)),
                ("date", models.DateField()),
                ("reason", models.TextField()),
                ("lines", models.JSONField(default=list)),
                ("subtotal", models.DecimalField(max_digits=14, decimal_places=2)),
                ("tax", models.DecimalField(max_digits=14, decimal_places=2)),
                ("total", models.DecimalField(max_digits=14, decimal_places=2)),
                ("payable_reduction", models.DecimalField(max_digits=14, decimal_places=2)),
                ("credit_amount", models.DecimalField(max_digits=14, decimal_places=2)),
                ("request_key", models.CharField(max_length=120)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="companies.company")),
                ("branch", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="branches.branch")),
                ("bill", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="debit_notes", to="inventory.supplierbill")),
                ("supplier", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="debit_notes", to="inventory.supplier")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="supplier_debit_notes", to="accounts.user")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="SupplierDebitApplication",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid4, editable=False, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("amount", models.DecimalField(max_digits=14, decimal_places=2)),
                ("request_key", models.CharField(max_length=120)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="companies.company")),
                ("branch", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="branches.branch")),
                ("bill", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="debit_credit_applications", to="inventory.supplierbill")),
                ("debit_note", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="applications", to="inventory.supplierdebitnote")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="accounts.user")),
            ],
        ),
        migrations.CreateModel(
            name="SupplierRefund",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid4, editable=False, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("amount", models.DecimalField(max_digits=14, decimal_places=2)),
                ("method", models.CharField(max_length=30)),
                ("reference", models.CharField(max_length=120, blank=True)),
                ("refunded_on", models.DateField()),
                ("request_key", models.CharField(max_length=120)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="companies.company")),
                ("branch", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="branches.branch")),
                ("supplier", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="refunds", to="inventory.supplier")),
                ("debit_note", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="refunds", blank=True, null=True, to="inventory.supplierdebitnote")),
                ("advance", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="refunds", blank=True, null=True, to="inventory.supplierpayment")),
                ("account", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="supplier_refunds", to="inventory.workshopfinancialaccount")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="accounts.user")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddConstraint(
            model_name="supplierdebitnote",
            constraint=models.UniqueConstraint(fields=["company", "supplier", "number"], name="unique_supplier_debit_number"),
        ),
        migrations.AddConstraint(
            model_name="supplierdebitnote",
            constraint=models.UniqueConstraint(fields=["company", "request_key"], name="unique_supplier_debit_request"),
        ),
        migrations.AddConstraint(
            model_name="supplierdebitapplication",
            constraint=models.UniqueConstraint(fields=["company", "request_key"], name="unique_supplier_debit_application"),
        ),
        migrations.AddConstraint(
            model_name="supplierrefund",
            constraint=models.UniqueConstraint(fields=["company", "request_key"], name="unique_supplier_refund_request"),
        ),
        migrations.AddConstraint(
            model_name="supplierrefund",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(debit_note__isnull=False, advance__isnull=True)
                    | models.Q(debit_note__isnull=True, advance__isnull=False)
                ),
                name="supplier_refund_exactly_one_source",
            ),
        ),
    ]
