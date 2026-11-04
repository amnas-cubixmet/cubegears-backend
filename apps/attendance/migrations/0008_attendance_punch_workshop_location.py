from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("attendance", "0007_attendance_daily_wage_finalization"),
    ]

    operations = [
        migrations.AddField(
            model_name="attendancerule", name="punch_location_mode",
            field=models.CharField(default="anywhere", max_length=16),
        ),
        migrations.AddField(
            model_name="attendancerule", name="workshop_latitude",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="attendancerule", name="workshop_longitude",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="attendancerule", name="punch_radius_meters",
            field=models.PositiveIntegerField(default=100),
        ),
    ]
