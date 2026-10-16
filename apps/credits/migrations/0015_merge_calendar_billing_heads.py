"""Merge the historical postpaid, deferred cycle and calendar policy branches.

No data operations: preserve all billing/audit tables while removing
multiple-leaf migration conflicts in existing installations.
"""
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("credits", "0012_calendar_billing_policy"),
        ("credits", "0012_credit_cycle_policy"),
        ("credits", "0014_monthly_feature_observation_cycle"),
    ]

    operations = []
