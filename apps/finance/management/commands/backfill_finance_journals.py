"""Explicit, idempotent source-to-GL backfill for existing workshops.

Dry-run by default; --apply requires a specific company ID and never changes
WorkshopAccountEntry, supplier bills, invoice totals or payment records.
An unclassified cash entry posts into suspense 9999 for manual review.
"""
import json

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.companies.models import Company
from apps.expenses.models import Expense
from apps.invoices.models import Invoice
from apps.inventory.models import (
    SupplierBill, SupplierDebitApplication, SupplierDebitNote,
    SupplierPaymentAllocation, WorkshopAccountEntry, WorkshopFinancialAccount,
)
from apps.finance.accounting_api import coverage
from apps.finance.ledger_engine import (
    journal_bank_entry, journal_debit_application, journal_debit_note,
    journal_expense, journal_invoice, journal_opening,
    journal_supplier_advance_allocation, journal_supplier_bill,
)
from apps.finance.models import JournalEntry


class Command(BaseCommand):
    help = "Audit or explicitly backfill balanced accounting journals for one workshop."

    def add_arguments(self, parser):
        parser.add_argument("--company", required=True, help="Existing workshop company UUID")
        parser.add_argument(
            "--apply", action="store_true", help="Post missing journal projections (default is dry-run)",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            company = Company.objects.select_for_update().get(pk=options["company"])
        except (Company.DoesNotExist, ValueError, TypeError) as exc:
            raise CommandError("Select a valid existing workshop company UUID.") from exc

        before = coverage(company)
        if not options["apply"]:
            self.stdout.write(json.dumps({
                "mode": "dry-run", "companyId": str(company.pk),
                "coverage": before, "mutation": False,
            }, default=str))
            return

        # Every source function uses a unique source key. Re-running this
        # command cannot post a second account movement or a second journal.
        original_count = JournalEntry.objects.filter(company=company).count()
        for account in WorkshopFinancialAccount.objects.filter(company=company).order_by("pk"):
            journal_opening(account)
        for row in WorkshopAccountEntry.objects.filter(
            company=company,
        ).select_related("account", "created_by").order_by("posted_at", "pk"):
            journal_bank_entry(row)
        for bill in SupplierBill.objects.filter(
            company=company, status="Posted",
        ).select_related("supplier", "created_by").order_by("posted_at", "pk"):
            journal_supplier_bill(bill)
        for note in SupplierDebitNote.objects.filter(
            company=company,
        ).select_related("bill").order_by("created_at", "pk"):
            journal_debit_note(note)
        for application in SupplierDebitApplication.objects.filter(
            company=company,
        ).select_related("bill").order_by("created_at", "pk"):
            journal_debit_application(application)
        for allocation in SupplierPaymentAllocation.objects.filter(
            company=company, payment__kind="Advance",
        ).select_related("bill", "payment").order_by("created_at", "pk"):
            journal_supplier_advance_allocation(allocation)
        for invoice in Invoice.objects.filter(
            company=company, kind="invoice", finalized_at__isnull=False,
        ).exclude(status="Cancelled").order_by("finalized_at", "pk"):
            journal_invoice(invoice)
        for expense in Expense.objects.filter(company=company).order_by("created_at", "pk"):
            journal_expense(expense)

        after = coverage(company)
        created = JournalEntry.objects.filter(company=company).count() - original_count
        self.stdout.write(json.dumps({
            "mode": "applied", "companyId": str(company.pk),
            "createdJournals": created,
            "coverageBefore": before, "coverageAfter": after,
            "note": (
                "No cash or source records changed. Cancelled historic invoices, "
                "unclassified suspense and unsupported historical records still "
                "require independent audit."
            ),
        }, default=str))
