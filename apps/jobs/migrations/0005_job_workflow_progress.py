from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("jobs", "0004_outside_labour"),
    ]

    operations = [
        migrations.AddField(
            model_name="job",
            name="workflow_progress",
            field=models.JSONField(default=dict, blank=True),
        ),
    ]
