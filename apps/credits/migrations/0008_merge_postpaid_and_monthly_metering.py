"""Merge the two non-overlapping credit billing migration branches.

0006_postpaid_usage_and_monthly_invoices and 0007_feature_monthly_metering
each create separate models. Both histories must be applied before this merge.
"""
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("credits", "0006_postpaid_usage_and_monthly_invoices"),
        ("credits", "0007_feature_monthly_metering"),
    ]

    operations = []
