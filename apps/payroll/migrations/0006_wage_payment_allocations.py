import uuid

import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [("payroll", "0005_daily_wage_ledger")]
    operations = [
        migrations.CreateModel(
            name="WagePaymentAllocation",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("work_date", models.DateField()),
                ("amount", models.DecimalField(max_digits=12, decimal_places=2)),
                ("company", models.ForeignKey(to="companies.company", on_delete=django.db.models.deletion.CASCADE)),
                ("branch", models.ForeignKey(to="branches.branch", on_delete=django.db.models.deletion.SET_NULL, null=True, blank=True)),
                ("employee", models.ForeignKey(to="employees.employee", on_delete=django.db.models.deletion.PROTECT, related_name="wage_payment_allocations")),
                ("payment", models.ForeignKey(to="payroll.wagepayment", on_delete=django.db.models.deletion.PROTECT, related_name="allocations")),
            ],
            options={"ordering": ["work_date"]},
        ),
        migrations.AddConstraint(
            model_name="wagepaymentallocation",
            constraint=models.UniqueConstraint(fields=["payment", "work_date"], name="unique_wage_payment_date_alloc"),
        ),
        migrations.AddConstraint(
            model_name="wagepaymentallocation",
            constraint=models.CheckConstraint(condition=Q(amount__gt=0), name="daily_wage_allocation_positive"),
        ),
    ]
