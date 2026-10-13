from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("inventory", "0002_initial")]

    operations = [
        migrations.AddField(
            model_name="stockmovement",
            name="unit_selling_price",
            field=models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0")),
        ),
    ]
