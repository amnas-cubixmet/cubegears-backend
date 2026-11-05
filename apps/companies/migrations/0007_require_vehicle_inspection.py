from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("companies", "0006_vercel_domain_management"),
    ]

    operations = [
        migrations.AddField(
            model_name="company",
            name="require_vehicle_inspection",
            field=models.BooleanField(default=False),
        ),
    ]
