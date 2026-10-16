from decimal import Decimal
import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Q
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ("credits", "0011_modulepricelabel"),
    ]
    operations = [
        migrations.CreateModel(
            name="CreditCyclePolicy",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("day_of_month", models.PositiveSmallIntegerField(default=5)),
                ("enabled", models.BooleanField(default=False)),
                ("block_overdue", models.BooleanField(default=True)),
                ("activated_at", models.DateTimeField(blank=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name="CreditCycleInvoice",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("category", models.CharField(choices=[("feature", "Feature"), ("storage", "Storage")], max_length=12)),
                ("starts_on", models.DateField()),
                ("ends_at", models.DateTimeField()),
                ("due_at", models.DateTimeField()),
                ("credits_due", models.DecimalField(decimal_places=2, default=Decimal("0"), max_digits=16)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("paid", "Paid"), ("insufficient", "Insufficient credits")], default="pending", max_length=14)),
                ("details", models.JSONField(blank=True, default=dict)),
                ("settled_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="cycle_invoices", to="companies.company")),
            ],
            options={"constraints": [models.UniqueConstraint(fields=("company", "category", "starts_on"), name="unique_company_cycle_usage")]},
        ),
        migrations.CreateModel(
            name="CycleLabelSession",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("started_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("ended_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("monthly_credits", models.DecimalField(decimal_places=2, max_digits=16)),
                ("user_seats", models.PositiveIntegerField(default=0)),
                ("bundle", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="cycle_sessions", to="credits.featurebundle")),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="cycle_label_sessions", to="companies.company")),
            ],
            options={"constraints": [models.UniqueConstraint(condition=Q(ended_at__isnull=True), fields=("company", "bundle"), name="unique_open_cycle_label")]},
        ),
    ]
