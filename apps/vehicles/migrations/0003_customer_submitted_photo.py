from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("vehicles", "0002_vehicle_inspection_photo"),
    ]

    operations = [
        migrations.AddField(
            model_name="vehicleinspectionphoto",
            name="submitted_by_customer",
            field=models.BooleanField(default=False, db_index=True),
        ),
    ]
