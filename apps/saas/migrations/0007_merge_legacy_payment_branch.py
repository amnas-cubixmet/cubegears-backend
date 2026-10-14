"""Join the two historic SaaS payment migration branches.

The tables have already been described in their original migrations;
this merge preserves those histories without destructive schema changes.
"""
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("saas", "0006_merge_storage_checkout"),
        ("saas", "0003_razorpay_payments"),
    ]

    operations = []
