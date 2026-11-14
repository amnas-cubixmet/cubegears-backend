"""Mark the preferred Cash or Bank/UPI account for automatic supplier settlements.

Existing accounts are unchanged. No default is guessed for companies with
multiple accounts; a sole active account remains auto-resolvable.
"""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0012_supplier_debit_notes_refunds"),
    ]

    operations = [
        migrations.AddField(
            model_name="workshopfinancialaccount",
            name="is_default_payment",
            field=models.BooleanField(default=False),
        ),
    ]
