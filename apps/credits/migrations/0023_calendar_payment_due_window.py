from django.db import migrations, models


def preserve_existing_due_days(apps, schema_editor):
    """Keep every existing policy's payment window unchanged by default."""
    BillingCyclePolicy = apps.get_model("credits", "BillingCyclePolicy")
    for policy in BillingCyclePolicy.objects.using(schema_editor.connection.alias).all():
        BillingCyclePolicy.objects.using(schema_editor.connection.alias).filter(
            pk=policy.pk
        ).update(due_end_day=policy.due_day)


class Migration(migrations.Migration):
    dependencies = [
        ("credits", "0022_storage_monitoring_readings"),
    ]

    operations = [
        migrations.AddField(
            model_name="billingcyclepolicy",
            name="due_end_day",
            field=models.PositiveSmallIntegerField(default=5),
        ),
        migrations.AddField(
            model_name="billingcyclestatement",
            name="grace_end_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(preserve_existing_due_days, migrations.RunPython.noop),
    ]
