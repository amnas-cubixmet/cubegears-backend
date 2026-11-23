from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [("customers", "0003_customer_document_references")]

    operations = [
        migrations.AddField(
            model_name="customer",
            name="offline_request_key",
            field=models.UUIDField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="customer",
            name="offline_payload_hash",
            field=models.CharField(blank=True, default="", editable=False, max_length=64),
        ),
        migrations.AddConstraint(
            model_name="customer",
            constraint=models.UniqueConstraint(
                fields=("company", "offline_request_key"),
                name="uniq_cust_offline_key_company",
            ),
        ),
    ]
