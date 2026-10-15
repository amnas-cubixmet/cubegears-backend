"""Merge the two pre-existing 0006 migration branches without data changes.

Both branches create independent tables. Keeping both dependencies ensures that
fresh installations and upgrades apply all postpaid and storage meter tables.
"""
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("credits", "0006_postpaid_usage_and_monthly_invoices"),
        ("credits", "0007_feature_monthly_metering"),
    ]
    operations = []
