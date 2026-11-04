from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("attendance", "0008_attendance_punch_workshop_location")]

    operations = [
        migrations.AddField(
            model_name="attendancerule",
            name="workshop_location_name",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
    ]
