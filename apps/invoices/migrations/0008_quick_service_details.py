# Quick Service can now retain optional customer/vehicle details and billable items.
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("invoices", "0007_quick_service"),
        ("jobs", "0008_job_service_type"),
        ("customers", "0003_customer_document_references"),
        ("vehicles", "0003_vehicle_catalog_entry"),
    ]

    operations = [
        migrations.AddField(
            model_name="quickservice",
            name="start_fingerprint",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="quickservice",
            name="customer",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name="quick_services", to="customers.customer",
            ),
        ),
        migrations.AddField(
            model_name="quickservice",
            name="vehicle",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name="quick_services", to="vehicles.vehicle",
            ),
        ),
        migrations.AddField(
            model_name="quickservice",
            name="items",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="quickservice",
            name="notes",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.RemoveConstraint(
            model_name="invoice",
            name="invoice_recipient_or_quick_service",
        ),
        migrations.AddConstraint(
            model_name="invoice",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(quick_service__isnull=True, customer__isnull=False)
                    | (
                        models.Q(quick_service__isnull=False, job__isnull=True, kind="invoice")
                        & (
                            models.Q(customer__isnull=True, vehicle__isnull=True)
                            | models.Q(customer__isnull=False)
                        )
                    )
                ),
                name="invoice_recipient_or_quick_service",
            ),
        ),
    ]
