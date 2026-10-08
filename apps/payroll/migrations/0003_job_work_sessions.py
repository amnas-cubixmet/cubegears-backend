import uuid
from decimal import Decimal
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("payroll", "0002_flexible_payroll_engine")]
    operations = []
