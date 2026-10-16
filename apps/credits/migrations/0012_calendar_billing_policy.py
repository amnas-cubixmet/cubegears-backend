from decimal import Decimal
import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("credits", "0011_modulepricelabel"),
        ("companies", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="BillingCyclePolicy",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("singleton", models.PositiveSmallIntegerField(default=1, editable=False, unique=True)),
                ("enabled", models.BooleanField(default=False)),
                ("due_day", models.PositiveSmallIntegerField(default=5)),
                ("restrict_overdue", models.BooleanField(default=True)),
                ("allow_zero_balance_usage", models.BooleanField(default=True)),
                ("effective_month", models.CharField(blank=True, default="", max_length=7)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name="BillingCycleStatement",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("month", models.CharField(max_length=7)),
                ("due_at", models.DateTimeField()),
                ("feature_credits", models.DecimalField(decimal_places=2, default=Decimal("0"), max_digits=16)),
                ("storage_credits", models.DecimalField(decimal_places=2, default=Decimal("0"), max_digits=16)),
                ("total_credits", models.DecimalField(decimal_places=2, default=Decimal("0"), max_digits=16)),
                ("details", models.JSONField(blank=True, default=dict)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("insufficient", "Insufficient credits"), ("paid", "Paid")], default="pending", max_length=12)),
                ("finalized_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("settled_at", models.DateTimeField(blank=True, null=True)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="calendar_billing_statements", to="companies.company")),
            ],
            options={"ordering": ["-month"]},
        ),
        migrations.AddConstraint(
            model_name="billingcyclestatement",
            constraint=models.UniqueConstraint(fields=("company", "month"), name="credits_one_calendar_statement_per_month"),
        ),
    ]
