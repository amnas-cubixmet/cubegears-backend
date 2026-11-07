from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("invoices", "0005_invoiceitem_tax_choice"),
    ]

    operations = [
        migrations.AddField(
            model_name="invoice",
            name="cancellation_reason",
            field=models.CharField(blank=True, default="", max_length=500),
        ),
    ]
