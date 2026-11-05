from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("employees","0004_employee_activity_relation_state")]

    operations = [
        migrations.AddField(model_name="employee", name="whatsapp_consent",
                            field=models.BooleanField(default=False)),
        migrations.AddField(model_name="employee", name="whatsapp_consent_source",
                            field=models.CharField(blank=True, max_length=120)),
    ]
