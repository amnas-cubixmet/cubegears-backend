from django.db import migrations, models
import apps.jobs.models


class Migration(migrations.Migration):
    dependencies = [("jobs", "0005_job_workflow_progress")]

    operations = [
        migrations.AlterField(
            model_name="jobphoto",
            name="url",
            field=models.URLField(blank=True),
        ),
        migrations.AddField(
            model_name="jobphoto",
            name="file",
            field=models.FileField(
                blank=True,
                storage=apps.jobs.models.job_photo_private_storage,
                upload_to=apps.jobs.models.job_photo_upload_to,
            ),
        ),
    ]
