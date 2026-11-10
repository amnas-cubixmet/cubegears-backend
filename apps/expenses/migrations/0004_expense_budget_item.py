"""Monthly budget item allocation for recorded workshop operating expenses."""
import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("expenses", "0003_expense_settlement"),
    ]

    operations = [
        migrations.CreateModel(
            name="ExpenseBudgetItem",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=120)),
                ("category", models.CharField(max_length=100)),
                ("month", models.DateField(help_text="First day of the budget month.")),
                ("planned_amount", models.DecimalField(decimal_places=2, max_digits=12)),
                ("is_active", models.BooleanField(default=True)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="companies.company")),
                ("branch", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="branches.branch")),
            ],
            options={
                "ordering": ["-month", "category", "name"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=["company", "month", "name"], name="unique_expense_budget_name_month",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(planned_amount__gte=0), name="expense_budget_nonnegative",
                    ),
                ],
            },
        ),
        migrations.AddField(
            model_name="expense",
            name="budget_item",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name="expenses", to="expenses.expensebudgetitem",
            ),
        ),
    ]
