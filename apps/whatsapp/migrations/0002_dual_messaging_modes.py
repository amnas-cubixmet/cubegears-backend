from django.db import migrations, models


def preserve_active_connections(apps, schema_editor):
    # Existing workshops already sending automatic notices must not be disrupted.
    Connection = apps.get_model("whatsapp", "WhatsAppBusinessConnection")
    Connection.objects.filter(connected=True, notifications_enabled=True).update(messaging_mode="automatic")


class Migration(migrations.Migration):
    dependencies = [("whatsapp", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="whatsappbusinessconnection",
            name="messaging_mode",
            field=models.CharField(
                max_length=12,
                choices=[("manual", "Manual"), ("automatic", "Automatic")],
                default="manual",
            ),
        ),
        migrations.RunPython(preserve_active_connections, migrations.RunPython.noop),
    ]
