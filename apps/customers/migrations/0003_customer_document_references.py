from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("customers", "0002_customer_portal_access")]

    operations = [
        migrations.AddField(
            model_name="customer",
            name="documents",
            field=models.JSONField(default=list, blank=True),
        ),
    ]
