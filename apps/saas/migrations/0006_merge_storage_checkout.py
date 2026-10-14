"""Merge independent storage-ledger and Razorpay subscription migration branches.

Both branches were already defined; this migration changes no tables or records.
"""
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("saas", "0003_storage_event"),
        ("saas", "0005_subscription_expiry_reminder"),
    ]

    operations = []
