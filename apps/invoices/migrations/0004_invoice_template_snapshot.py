from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("invoices", "0003_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="invoice",
            name="template_snapshot",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
