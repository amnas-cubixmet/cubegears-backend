from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("inventory", "0010_part12_branch_balances")]

    operations = [
        migrations.AddField(
            model_name="stockcategory",
            name="parent",
            field=models.ForeignKey(
                to="inventory.stockcategory", on_delete=django.db.models.deletion.PROTECT,
                related_name="subcategories", null=True, blank=True,
            ),
        ),
    ]
