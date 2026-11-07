from decimal import Decimal

from django.db import migrations, models
import django.db.models.deletion


def initialize_branch_balances(apps, schema_editor):
    StockItem = apps.get_model("inventory", "StockItem")
    StockBranchBalance = apps.get_model("inventory", "StockBranchBalance")
    for item in StockItem.objects.exclude(branch_id__isnull=True).iterator(chunk_size=500):
        StockBranchBalance.objects.get_or_create(
            company_id=item.company_id,
            item_id=item.pk,
            branch_id=item.branch_id,
            defaults={"quantity": item.on_hand, "reserved": item.reserved},
        )


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0009_financial_account_other_type"),
    ]

    operations = [
        migrations.CreateModel(
            name="StockBranchBalance",
            fields=[
                ("id", models.UUIDField(primary_key=True, editable=False, default=__import__("uuid").uuid4)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("quantity", models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))),
                ("reserved", models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="companies.company")),
                ("branch", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="inventory_balances", to="branches.branch")),
                ("item", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="branch_balances", to="inventory.stockitem")),
            ],
        ),
        migrations.AddField(
            model_name="stockmovement",
            name="request_key",
            field=models.CharField(max_length=120, null=True, blank=True, default=None),
        ),
        migrations.AddField(
            model_name="stockmovement",
            name="origin_movement",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, null=True, blank=True, related_name="return_movements", to="inventory.stockmovement"),
        ),
        migrations.AddField(
            model_name="stocktransfer",
            name="request_key",
            field=models.CharField(max_length=120, null=True, blank=True, default=None),
        ),
        migrations.AddConstraint(
            model_name="stockbranchbalance",
            constraint=models.UniqueConstraint(fields=("company", "item", "branch"), name="unique_inventory_branch_balance"),
        ),
        migrations.AddConstraint(
            model_name="stockbranchbalance",
            constraint=models.CheckConstraint(condition=models.Q(quantity__gte=0), name="inventory_branch_nonnegative"),
        ),
        migrations.AddConstraint(
            model_name="stockbranchbalance",
            constraint=models.CheckConstraint(condition=models.Q(reserved__gte=0), name="inventory_branch_reserved_nonnegative"),
        ),
        migrations.AddConstraint(
            model_name="stockmovement",
            constraint=models.UniqueConstraint(fields=("company", "request_key"), name="unique_inventory_movement_request"),
        ),
        migrations.AddConstraint(
            model_name="stocktransfer",
            constraint=models.UniqueConstraint(fields=("company", "request_key"), name="unique_inventory_transfer_request"),
        ),
        migrations.RunPython(initialize_branch_balances, migrations.RunPython.noop),
    ]
