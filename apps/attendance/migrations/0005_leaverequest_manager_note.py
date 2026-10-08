from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0004_attendance_rule_tracking_notifications"),
    ]

    operations = [
        migrations.AddField(
            model_name="leaverequest",
            name="manager_note",
            field=models.TextField(blank=True),
        ),
    ]
