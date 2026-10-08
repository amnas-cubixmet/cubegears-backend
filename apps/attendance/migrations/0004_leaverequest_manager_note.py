from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0003_attendance_modes_and_sessions"),
    ]

    operations = [
        migrations.AddField(
            model_name="leaverequest",
            name="manager_note",
            field=models.TextField(blank=True),
        ),
    ]
