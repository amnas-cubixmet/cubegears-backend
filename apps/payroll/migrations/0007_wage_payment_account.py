import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("payroll", "0006_wage_payment_allocations"),
        ("inventory", "0008_workshopfinancialaccount_account_number_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="wagepayment",
            name="account",
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="daily_wage_payments",
                to="inventory.workshopfinancialaccount",
            ),
        ),
    ]
