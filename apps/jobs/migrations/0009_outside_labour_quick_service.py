# Outside labour can be attached to either a Job Card or a Quick Service.
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("jobs", "0008_job_service_type"),
        ("invoices", "0008_quick_service_details"),
    ]

    operations = [
        migrations.AlterField(
            model_name="outsidelabourcharge",
            name="job",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name="outside_labour", to="jobs.job",
            ),
        ),
        migrations.AddField(
            model_name="outsidelabourcharge",
            name="quick_service",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name="outside_labour", to="invoices.quickservice",
            ),
        ),
        migrations.AddConstraint(
            model_name="outsidelabourcharge",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(job__isnull=False, quick_service__isnull=True)
                    | models.Q(job__isnull=True, quick_service__isnull=False)
                ),
                name="outside_labour_exactly_one_source",
            ),
        ),
    ]
