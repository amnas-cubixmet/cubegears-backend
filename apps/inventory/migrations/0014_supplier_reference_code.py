"""Backfill stable, company-scoped Supplier reference codes without changing UUIDs."""
from django.db import migrations, models
import django.db.models.deletion


def backfill_supplier_codes(apps, schema_editor):
    Supplier = apps.get_model("inventory", "Supplier")
    Sequence = apps.get_model("inventory", "SupplierCodeSequence")
    last_company = None
    sequence_number = 0
    for supplier_id, company_id in Supplier.objects.order_by(
        "company_id", "created_at", "pk"
    ).values_list("pk", "company_id").iterator(chunk_size=500):
        if company_id != last_company:
            if last_company is not None:
                Sequence.objects.update_or_create(
                    company_id=last_company,
                    defaults={"next_number": sequence_number + 1},
                )
            last_company = company_id
            sequence_number = 0
        sequence_number += 1
        Supplier.objects.filter(pk=supplier_id).update(
            code=f"SUP-{sequence_number:05d}",
        )
    if last_company is not None:
        Sequence.objects.update_or_create(
            company_id=last_company,
            defaults={"next_number": sequence_number + 1},
        )


class Migration(migrations.Migration):
    dependencies = [("inventory", "0013_financial_account_default_payment")]

    operations = [
        migrations.AddField(
            model_name="supplier",
            name="code",
            field=models.CharField(max_length=20, blank=True, default=""),
        ),
        migrations.CreateModel(
            name="SupplierCodeSequence",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("next_number", models.PositiveIntegerField(default=1)),
                ("company", models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="supplier_code_sequence",
                    to="companies.company",
                )),
            ],
        ),
        migrations.RunPython(backfill_supplier_codes, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="supplier",
            constraint=models.UniqueConstraint(
                fields=("company", "code"),
                name="supplier_code_unique_within_company",
            ),
        ),
    ]
