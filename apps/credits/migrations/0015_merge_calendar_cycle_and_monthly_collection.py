"""Merge the three independent 0012 billing migration branches.

All prior migrations are preserved; no duplicate tables or data loss.
"""
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("credits", "0012_calendar_billing_policy"),
        ("credits", "0012_credit_cycle_policy"),
        ("credits", "0014_monthly_feature_observation_cycle"),
    ]

    operations = []
