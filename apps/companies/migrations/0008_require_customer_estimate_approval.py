from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("companies", "0007_require_vehicle_inspection")]

    operations = [
        migrations.AddField(
            model_name="company",
            name="require_customer_estimate_approval",
            field=models.BooleanField(default=False),
        ),
    ]
