"""Add independent manufacturer part numbers without modifying existing SKUs."""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0004_supplier_credit_ledger"),
    ]

    operations = [
        migrations.AddField(
            model_name="stockitem",
            name="part_number",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
    ]
