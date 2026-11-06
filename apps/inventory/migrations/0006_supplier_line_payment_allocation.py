"""Add item-level payment detail without changing existing bill/payment ledger rows."""
import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0005_stockitem_part_number"),
    ]

    operations = [
        migrations.CreateModel(
            name="SupplierLinePaymentAllocation",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("branch", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="branches.branch")),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="companies.company")),
                ("allocation", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="line_allocations", to="inventory.supplierpaymentallocation")),
                ("line_index", models.PositiveIntegerField()),
                ("amount", models.DecimalField(decimal_places=2, max_digits=14)),
            ],
        ),
        migrations.AddConstraint(
            model_name="supplierlinepaymentallocation",
            constraint=models.UniqueConstraint(fields=("allocation", "line_index"), name="unique_supplier_bill_line_application"),
        ),
    ]
