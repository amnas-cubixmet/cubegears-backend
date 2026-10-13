import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import apps.jobs.models


class Migration(migrations.Migration):
    dependencies = [
        ("jobs", "0006_jobphoto_private_file"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="OutsidePartBill",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("purchase_id", models.UUIDField(db_index=True)),
                ("file", models.FileField(
                    storage=apps.jobs.models.job_photo_private_storage,
                    upload_to=apps.jobs.models.outside_part_bill_upload_to,
                    max_length=255,
                )),
                ("original_filename", models.CharField(max_length=180)),
                ("branch", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    to="branches.branch",
                )),
                ("company", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE, to="companies.company",
                )),
                ("job", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE, related_name="outside_part_bills", to="jobs.job",
                )),
                ("uploaded_by", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
        ),
        migrations.AddConstraint(
            model_name="outsidepartbill",
            constraint=models.UniqueConstraint(
                fields=("company", "job", "purchase_id"),
                name="unique_outside_part_bill_per_purchase",
            ),
        ),
    ]
