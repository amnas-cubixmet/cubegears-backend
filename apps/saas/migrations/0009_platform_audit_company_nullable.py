from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("saas", "0008_alter_storageevent_event_type"),
    ]

    operations = [
        migrations.AlterField(
            model_name="securityevent",
            name="company",
            field=models.ForeignKey(
                to="companies.company", on_delete=django.db.models.deletion.CASCADE,
                blank=True, null=True,
            ),
        ),
    ]
