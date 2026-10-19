from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("credits", "0017_billingcyclepolicy_calculation_interval_seconds_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="creditpackage",
            name="is_archived",
            field=models.BooleanField(default=False, db_index=True),
        ),
    ]
