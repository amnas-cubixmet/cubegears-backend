from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("invoices", "0004_invoice_template_snapshot"),
    ]

    operations = [
        migrations.AddField(
            model_name="invoiceitem",
            name="tax_choice",
            field=models.CharField(max_length=16, blank=True, default=""),
        ),
    ]
