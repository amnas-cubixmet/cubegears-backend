"""Join two existing no-op credits merge migrations without changing tables.

Both 0008 revisions are already valid migration history and may exist on
separate deployments; retain them and declare one shared successor.
"""
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("credits", "0008_merge_postpaid_and_monthly_metering"),
        ("credits", "0008_merge_postpaid_metering"),
    ]

    operations = []
