"""Additive accounting tables. No financial records are rewritten or deleted."""
import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True
    dependencies = [
        ("accounts", "0002_mobile_login_staff_tokens"),
        ("companies", "0008_require_customer_estimate_approval"),
        ("inventory", "0012_supplier_debit_notes_refunds"),
    ]

    operations = [
        migrations.CreateModel(
            name="LedgerAccount",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("code", models.CharField(max_length=80)),
                ("name", models.CharField(max_length=160)),
                ("account_type", models.CharField(
                    choices=[(x, x) for x in ("Asset", "Liability", "Equity", "Income", "Expense")],
                    max_length=12,
                )),
                ("is_system", models.BooleanField(default=True)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="companies.company")),
                ("financial_account", models.OneToOneField(
                    to="inventory.workshopfinancialaccount", on_delete=django.db.models.deletion.PROTECT,
                    null=True, blank=True, related_name="general_ledger_account",
                )),
            ],
            options={"ordering": ["code"]},
        ),
        migrations.CreateModel(
            name="JournalEntry",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("source_key", models.CharField(max_length=180)),
                ("reference", models.CharField(max_length=160, blank=True)),
                ("memo", models.CharField(max_length=500)),
                ("posted_at", models.DateTimeField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("entry_kind", models.CharField(max_length=30, default="Automatic")),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="companies.company")),
                ("created_by", models.ForeignKey(
                    to="accounts.user", on_delete=django.db.models.deletion.SET_NULL,
                    null=True, blank=True,
                )),
            ],
            options={"ordering": ["-posted_at", "-created_at"]},
        ),
        migrations.CreateModel(
            name="JournalLine",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("debit", models.DecimalField(max_digits=15, decimal_places=2, default=0)),
                ("credit", models.DecimalField(max_digits=15, decimal_places=2, default=0)),
                ("account", models.ForeignKey(
                    to="finance.ledgeraccount", on_delete=django.db.models.deletion.PROTECT,
                    related_name="journal_lines",
                )),
                ("journal", models.ForeignKey(
                    to="finance.journalentry", on_delete=django.db.models.deletion.PROTECT,
                    related_name="lines",
                )),
            ],
        ),
        migrations.CreateModel(
            name="BankReconciliation",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("statement_date", models.DateField()),
                ("statement_balance", models.DecimalField(max_digits=15, decimal_places=2)),
                ("cleared_balance", models.DecimalField(max_digits=15, decimal_places=2)),
                ("book_balance", models.DecimalField(max_digits=15, decimal_places=2)),
                ("difference", models.DecimalField(max_digits=15, decimal_places=2)),
                ("reference", models.CharField(max_length=160, blank=True)),
                ("request_key", models.CharField(max_length=120)),
                ("posted_at", models.DateTimeField(auto_now_add=True)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="companies.company")),
                ("account", models.ForeignKey(
                    to="inventory.workshopfinancialaccount", on_delete=django.db.models.deletion.PROTECT,
                    related_name="statement_reconciliations",
                )),
                ("created_by", models.ForeignKey(
                    to="accounts.user", on_delete=django.db.models.deletion.SET_NULL,
                    null=True, blank=True,
                )),
            ],
            options={"ordering": ["-statement_date"]},
        ),
        migrations.CreateModel(
            name="BankReconciliationLine",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("entry", models.OneToOneField(
                    to="inventory.workshopaccountentry", on_delete=django.db.models.deletion.PROTECT,
                    related_name="bank_reconciliation_line",
                )),
                ("reconciliation", models.ForeignKey(
                    to="finance.bankreconciliation", on_delete=django.db.models.deletion.PROTECT,
                    related_name="items",
                )),
            ],
        ),
        migrations.AddConstraint(
            model_name="ledgeraccount",
            constraint=models.UniqueConstraint(fields=["company", "code"], name="unique_workshop_gl_code"),
        ),
        migrations.AddConstraint(
            model_name="journalentry",
            constraint=models.UniqueConstraint(fields=["company", "source_key"], name="unique_workshop_gl_source"),
        ),
        migrations.AddConstraint(
            model_name="journalline",
            constraint=models.CheckConstraint(
                condition=models.Q(debit__gte=0, credit__gte=0), name="gl_nonnegative_lines",
            ),
        ),
        migrations.AddConstraint(
            model_name="journalline",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(debit__gt=0, credit=0) | models.Q(credit__gt=0, debit=0)
                ), name="gl_line_one_side_only",
            ),
        ),
        migrations.AddConstraint(
            model_name="bankreconciliation",
            constraint=models.UniqueConstraint(
                fields=["company", "request_key"], name="unique_bank_reconcile_request",
            ),
        ),
    ]
