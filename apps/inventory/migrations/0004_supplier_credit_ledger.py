"""Supplier bills and account ledger: additive financial records, preserving legacy data."""
from decimal import Decimal
import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0003_stockmovement_unit_selling_price"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="WorkshopFinancialAccount",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, serialize=False, editable=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("branch", models.ForeignKey(to="branches.branch", null=True, blank=True, on_delete=django.db.models.deletion.SET_NULL)),
                ("company", models.ForeignKey(to="companies.company", on_delete=django.db.models.deletion.CASCADE)),
                ("name", models.CharField(max_length=120)),
                ("kind", models.CharField(max_length=16, choices=[("Cash","Cash"),("Bank","Bank"),("UPI","UPI")])),
                ("opening_balance", models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))),
                ("is_active", models.BooleanField(default=True)),
                ("created_by", models.ForeignKey(to="accounts.user", on_delete=django.db.models.deletion.SET_NULL, null=True, blank=True)),
            ],
            
        ),
        migrations.CreateModel(
            name="SupplierBill",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, serialize=False, editable=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("branch", models.ForeignKey(to="branches.branch", null=True, blank=True, on_delete=django.db.models.deletion.SET_NULL)),
                ("company", models.ForeignKey(to="companies.company", on_delete=django.db.models.deletion.CASCADE)),
                ("invoice_number", models.CharField(max_length=100)),
                ("invoice_date", models.DateField()),
                ("due_date", models.DateField(null=True, blank=True)),
                ("items", models.JSONField(default=list)),
                ("subtotal", models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))),
                ("tax", models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))),
                ("total", models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))),
                ("status", models.CharField(max_length=12, default="Draft", choices=[("Draft","Draft"),("Posted","Posted")])),
                ("receive_into_stock", models.BooleanField(default=False)),
                ("posted_at", models.DateTimeField(null=True, blank=True)),
                ("supplier", models.ForeignKey(to="inventory.supplier", on_delete=django.db.models.deletion.PROTECT, related_name="accounting_bills")),
                ("related_job", models.ForeignKey(to="jobs.job", on_delete=django.db.models.deletion.PROTECT, null=True, blank=True)),
                ("created_by", models.ForeignKey(to="accounts.user", on_delete=django.db.models.deletion.SET_NULL, null=True, blank=True, related_name="supplier_bills_created")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="SupplierPayment",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, serialize=False, editable=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("branch", models.ForeignKey(to="branches.branch", null=True, blank=True, on_delete=django.db.models.deletion.SET_NULL)),
                ("company", models.ForeignKey(to="companies.company", on_delete=django.db.models.deletion.CASCADE)),
                ("amount", models.DecimalField(max_digits=14, decimal_places=2)),
                ("kind", models.CharField(max_length=12, choices=[("Payment","Payment"),("Advance","Advance")])),
                ("method", models.CharField(max_length=30)),
                ("reference", models.CharField(max_length=120, blank=True)),
                ("paid_on", models.DateField()),
                ("idempotency_key", models.CharField(max_length=120)),
                ("supplier", models.ForeignKey(to="inventory.supplier", on_delete=django.db.models.deletion.PROTECT, related_name="accounting_payments")),
                ("account", models.ForeignKey(to="inventory.workshopfinancialaccount", on_delete=django.db.models.deletion.PROTECT, related_name="supplier_payments")),
                ("created_by", models.ForeignKey(to="accounts.user", on_delete=django.db.models.deletion.SET_NULL, null=True, blank=True, related_name="supplier_payments_recorded")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="SupplierPaymentAllocation",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, serialize=False, editable=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("branch", models.ForeignKey(to="branches.branch", null=True, blank=True, on_delete=django.db.models.deletion.SET_NULL)),
                ("company", models.ForeignKey(to="companies.company", on_delete=django.db.models.deletion.CASCADE)),
                ("amount", models.DecimalField(max_digits=14, decimal_places=2)),
                ("payment", models.ForeignKey(to="inventory.supplierpayment", on_delete=django.db.models.deletion.PROTECT, related_name="allocations")),
                ("bill", models.ForeignKey(to="inventory.supplierbill", on_delete=django.db.models.deletion.PROTECT, related_name="payment_allocations")),
            ],
            
        ),
        migrations.CreateModel(
            name="WorkshopAccountEntry",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, serialize=False, editable=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("branch", models.ForeignKey(to="branches.branch", null=True, blank=True, on_delete=django.db.models.deletion.SET_NULL)),
                ("company", models.ForeignKey(to="companies.company", on_delete=django.db.models.deletion.CASCADE)),
                ("amount", models.DecimalField(max_digits=14, decimal_places=2)),
                ("entry_type", models.CharField(max_length=30)),
                ("source_key", models.CharField(max_length=160)),
                ("reference", models.CharField(max_length=120, blank=True)),
                ("posted_at", models.DateTimeField()),
                ("account", models.ForeignKey(to="inventory.workshopfinancialaccount", on_delete=django.db.models.deletion.PROTECT, related_name="entries")),
                ("created_by", models.ForeignKey(to="accounts.user", on_delete=django.db.models.deletion.SET_NULL, null=True, blank=True)),
            ],
            options={"ordering": ["posted_at", "created_at"]},
        ),
        migrations.AddConstraint(model_name="workshopfinancialaccount", constraint=models.UniqueConstraint(fields=["company","name"], name="unique_workshop_money_account")),
        migrations.AddConstraint(model_name="supplierbill", constraint=models.UniqueConstraint(fields=["company","supplier","invoice_number"], name="unique_supplier_bill_reference_per_company")),
        migrations.AddConstraint(model_name="supplierpayment", constraint=models.UniqueConstraint(fields=["company","idempotency_key"], name="unique_supplier_payment_request")),
        migrations.AddConstraint(model_name="supplierpaymentallocation", constraint=models.UniqueConstraint(fields=["payment","bill"], name="unique_payment_allocation_per_bill")),
        migrations.AddConstraint(model_name="workshopaccountentry", constraint=models.UniqueConstraint(fields=["company","source_key"], name="unique_workshop_account_posting")),
    ]
