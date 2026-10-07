from django.db import migrations, models


def backfill_location_tracking(apps, schema_editor):
    AttendanceRule = apps.get_model("attendance", "AttendanceRule")
    AttendanceRule.objects.filter(location_required=True).update(location_tracking_enabled=True)


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0003_attendance_modes_and_sessions"),
    ]

    operations = [
        migrations.AddField(
            model_name="attendancerule",
            name="location_tracking_enabled",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="attendancerule",
            name="missing_punch_reminder_enabled",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="attendancerule",
            name="missing_punch_reminder_minutes",
            field=models.PositiveIntegerField(default=15),
        ),
        migrations.AddField(
            model_name="attendancerule",
            name="leave_request_notifications",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="attendancerule",
            name="overtime_request_notifications",
            field=models.BooleanField(default=True),
        ),
        migrations.RunPython(backfill_location_tracking, migrations.RunPython.noop),
    ]
