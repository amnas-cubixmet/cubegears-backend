"""Immutable double-entry accounting overlay for existing workshop finance."""
from decimal import Decimal
import uuid

from django.db import models
from django.db.models import Q


class LedgerAccount(models.Model):
    TYPES = [(v, v) for v in ("Asset", "Liability", "Equity", "Income", "Expense")]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey("companies.Company", on_delete=models.CASCADE)
    code = models.CharField(max_length=80)
    name = models.CharField(max_length=160)
    account_type = models.CharField(max_length=12, choices=TYPES)
    is_system = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    financial_account = models.OneToOneField(
        "inventory.WorkshopFinancialAccount", on_delete=models.PROTECT,
        null=True, blank=True, related_name="general_ledger_account",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["code"]
        constraints = [
            models.UniqueConstraint(fields=["company", "code"], name="unique_workshop_gl_code"),
        ]


class JournalEntry(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey("companies.Company", on_delete=models.CASCADE)
    source_key = models.CharField(max_length=180)
    reference = models.CharField(max_length=160, blank=True)
    memo = models.CharField(max_length=500)
    posted_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True, blank=True)
    entry_kind = models.CharField(max_length=30, default="Automatic")

    class Meta:
        ordering = ["-posted_at", "-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["company", "source_key"], name="unique_workshop_gl_source"),
        ]


class JournalLine(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    journal = models.ForeignKey(JournalEntry, on_delete=models.PROTECT, related_name="lines")
    account = models.ForeignKey(LedgerAccount, on_delete=models.PROTECT, related_name="journal_lines")
    debit = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal("0"))
    credit = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal("0"))

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(debit__gte=0, credit__gte=0), name="gl_nonnegative_lines"),
            models.CheckConstraint(
                condition=(
                    Q(debit__gt=0, credit=0) | Q(credit__gt=0, debit=0)
                ), name="gl_line_one_side_only",
            ),
        ]


class BankReconciliation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey("companies.Company", on_delete=models.CASCADE)
    account = models.ForeignKey(
        "inventory.WorkshopFinancialAccount", on_delete=models.PROTECT,
        related_name="statement_reconciliations",
    )
    statement_date = models.DateField()
    statement_balance = models.DecimalField(max_digits=15, decimal_places=2)
    cleared_balance = models.DecimalField(max_digits=15, decimal_places=2)
    book_balance = models.DecimalField(max_digits=15, decimal_places=2)
    difference = models.DecimalField(max_digits=15, decimal_places=2)
    reference = models.CharField(max_length=160, blank=True)
    request_key = models.CharField(max_length=120)
    posted_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ["-statement_date"]
        constraints = [
            models.UniqueConstraint(fields=["company", "request_key"], name="unique_bank_reconcile_request"),
        ]


class BankReconciliationLine(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reconciliation = models.ForeignKey(
        BankReconciliation, on_delete=models.PROTECT, related_name="items",
    )
    entry = models.OneToOneField(
        "inventory.WorkshopAccountEntry", on_delete=models.PROTECT,
        related_name="bank_reconciliation_line",
    )
