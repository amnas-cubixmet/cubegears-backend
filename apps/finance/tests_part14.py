"""Part 14 accounting controls: balanced journals, cash projection and bank reconciliation."""
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.companies.models import Company
from apps.finance.models import (
    BankReconciliation, JournalEntry, JournalLine, LedgerAccount,
)
from apps.inventory.models import WorkshopAccountEntry, WorkshopFinancialAccount
from apps.roles.models import Role


@override_settings(
    CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
    CREDIT_LABEL_ONLY_ACCESS_ENABLED=False,
)
class Part14DoubleEntryTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Double Entry Garage", slug="part14-double-entry",
        )
        role = Role.objects.create(
            company=self.company, name="Owner", code="PART14_OWNER",
            permissions=["*"], is_active=True, is_system=True,
        )
        self.owner = User.objects.create_user(
            email="part14@example.test", name="Accounting Owner",
            password="NotForProduction123", company=self.company, role=role,
        )
        self.company.owner = self.owner
        self.company.save(update_fields=["owner"])
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.cash = WorkshopFinancialAccount.objects.create(
            company=self.company, name="Cash Till", kind="Cash",
            opening_balance=Decimal("1000.00"), created_by=self.owner,
        )
        self.bank = WorkshopFinancialAccount.objects.create(
            company=self.company, name="Company Bank", kind="Bank",
            opening_balance=Decimal("500.00"), created_by=self.owner,
        )

    def test_openings_and_trial_balance_are_balanced_without_cash_mutation(self):
        trial = self.client.get("/api/v1/finance/statements/trial-balance")
        self.assertEqual(trial.status_code, 200, getattr(trial, "data", None))
        self.assertTrue(trial.data["isBalanced"])
        self.assertEqual(trial.data["totalDebit"], "1500.00")
        self.assertEqual(trial.data["totalCredit"], "1500.00")
        self.assertEqual(trial.data["coverage"]["missingSourceCount"], 0)
        self.assertEqual(WorkshopAccountEntry.objects.count(), 0)
        statement = self.client.get("/api/v1/finance/statements/balance-sheet")
        self.assertEqual(statement.status_code, 200, getattr(statement, "data", None))
        self.assertTrue(statement.data["isBalanced"])
        self.assertEqual(statement.data["assets"], "1500.00")
        self.assertEqual(statement.data["liabilitiesAndEquity"], "1500.00")
        self.assertEqual(JournalEntry.objects.count(), 2)

    def test_manual_journal_balanced_and_idempotent_with_non_cash_accounts(self):
        payload = {
            "memo": "Allocate retained earnings to operating costs",
            "reference": "MANUAL-001", "requestKey": "part14-journal-0001",
            "lines": [
                {"code": "5100", "debit": "50.00", "credit": "0"},
                {"code": "3000", "debit": "0", "credit": "50.00"},
            ],
        }
        original = self.client.post("/api/v1/finance/journals", payload, format="json")
        self.assertEqual(original.status_code, 201, getattr(original, "data", None))
        self.assertEqual(len(original.data["lines"]), 2)
        self.assertEqual(self.client.post(
            "/api/v1/finance/journals", payload, format="json",
        ).status_code, 200)
        changed = self.client.post("/api/v1/finance/journals", {
            **payload, "memo": "Different memo for the same request",
        }, format="json")
        self.assertEqual(changed.status_code, 400)
        invalid = self.client.post("/api/v1/finance/journals", {
            **payload, "requestKey": "part14-journal-unbalanced",
            "lines": [{"code": "5100", "debit": "40.00", "credit": "0"},
                      {"code": "3000", "credit": "30.00", "debit": "0"}],
        }, format="json")
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(JournalEntry.objects.count(), 3)
        self.assertEqual(JournalLine.objects.count(), 6)
        profit = self.client.get("/api/v1/finance/statements/profit-loss")
        self.assertEqual(profit.data["netProfit"], "-50.00")
        self.assertEqual(WorkshopAccountEntry.objects.count(), 0)

    def test_bank_statement_requires_correct_cleared_balance(self):
        entry = WorkshopAccountEntry.objects.create(
            company=self.company, account=self.bank,
            amount=Decimal("150.00"), entry_type="manual_adjustment",
            source_key="part14-adjust-account-once",
            posted_at=timezone.now(), created_by=self.owner,
        )
        candidates = self.client.get("/api/v1/finance/bank-reconciliations/candidates", {
            "accountId": str(self.bank.pk),
        })
        self.assertEqual(candidates.status_code, 200, getattr(candidates, "data", None))
        self.assertEqual(len(candidates.data), 1)
        self.assertEqual(candidates.data[0]["id"], str(entry.pk))
        base = {
            "accountId": str(self.bank.pk), "statementDate": timezone.localdate().isoformat(),
            "statementBalance": "650.00", "reference": "BANK-STMT-01",
            "entryIds": [str(entry.pk)], "requestKey": "part14-bank-reconcile-0001",
        }
        failed = self.client.post("/api/v1/finance/bank-reconciliations", {
            **base, "statementBalance": "700.00",
        }, format="json")
        self.assertEqual(failed.status_code, 400)
        result = self.client.post("/api/v1/finance/bank-reconciliations", base, format="json")
        self.assertEqual(result.status_code, 201, getattr(result, "data", None))
        self.assertEqual(result.data["unclearedDifference"], "0.00")
        again = self.client.post("/api/v1/finance/bank-reconciliations", base, format="json")
        self.assertEqual(again.status_code, 200, getattr(again, "data", None))
        self.assertEqual(BankReconciliation.objects.count(), 1)
        self.assertEqual(self.client.post("/api/v1/finance/bank-reconciliations", {
            **base, "requestKey": "part14-bank-reconcile-0002",
        }, format="json").status_code, 400)
        self.assertEqual(WorkshopAccountEntry.objects.count(), 1)

    def test_cash_movements_are_mirrored_once_and_uncategorized_are_disclosed(self):
        entry = WorkshopAccountEntry.objects.create(
            company=self.company, account=self.cash,
            amount=Decimal("-100.00"), entry_type="mystery_legacy",
            source_key="part14-unknown-ledger-1", created_by=self.owner,
            posted_at=timezone.now(),
        )
        journal = JournalEntry.objects.get(
            company=self.company, source_key="cash:" + str(entry.pk),
        )
        self.assertEqual(journal.lines.count(), 2)
        self.assertEqual(
            sum((line.debit - line.credit for line in journal.lines.all()), Decimal("0")),
            Decimal("0"),
        )
        overview = self.client.get("/api/v1/finance/journals")
        self.assertEqual(overview.status_code, 200)
        self.assertFalse(overview.data["coverage"]["isComplete"])
        self.assertEqual(overview.data["coverage"]["unclassifiedLineCount"], 1)
        self.assertEqual(
            LedgerAccount.objects.get(company=self.company, code="9999").account_type,
            "Equity",
        )

    def test_invoice_posts_gst_and_income_not_receipt_twice(self):
        from apps.customers.models import Customer
        from apps.invoices.models import Invoice

        customer = Customer.objects.create(
            company=self.company, name="Ledger Customer",
        )
        invoice = Invoice.objects.create(
            company=self.company, customer=customer, number="PART14-INV-01",
            date=timezone.localdate(), kind="invoice", status="Finalized",
            finalized_at=timezone.now(), taxable=Decimal("100.00"),
            cgst=Decimal("9.00"), sgst=Decimal("9.00"),
            total=Decimal("118.00"), balance=Decimal("118.00"),
        )
        journal = JournalEntry.objects.get(
            company=self.company, source_key="invoice:" + str(invoice.pk),
        )
        amounts = {
            line.account.code: (line.debit, line.credit)
            for line in journal.lines.select_related("account")
        }
        self.assertEqual(amounts["1100"], (Decimal("118.00"), Decimal("0")))
        self.assertEqual(amounts["4000"], (Decimal("0"), Decimal("100.00")))
        self.assertEqual(amounts["2150"], (Decimal("0"), Decimal("18.00")))
        self.assertEqual(self.client.get("/api/v1/finance/statements/trial-balance").data["isBalanced"], True)

    def test_balance_reports_reject_payment_clerk_without_explicit_grant(self):
        role = Role.objects.create(
            company=self.company, name="Payment Clerk", code="PART14_CLERK",
            permissions=["payments.view"], is_active=True,
        )
        user = User.objects.create_user(
            email="part14-clerk@example.test", name="Clerk",
            password="NoRealPassword123", company=self.company, role=role,
        )
        self.client.force_authenticate(user)
        for endpoint in (
            "/api/v1/finance/chart", "/api/v1/finance/journals",
            "/api/v1/finance/statements/balance-sheet",
            "/api/v1/finance/bank-reconciliations",
        ):
            reply = self.client.get(endpoint)
            self.assertEqual(reply.status_code, 403, (endpoint, getattr(reply, "data", None)))

    def test_draft_expense_is_not_profit_and_status_changes_reconcile_once(self):
        from apps.expenses.models import Expense
        draft = Expense.objects.create(
            company=self.company, date=timezone.localdate(),
            category="Workshop Rent", description="Unapproved draft",
            amount=Decimal("240.00"), status="Draft", created_by=self.owner,
        )
        root = "expense:" + str(draft.pk) + ":revision:"
        self.assertEqual(JournalEntry.objects.filter(
            company=self.company, source_key__startswith=root,
        ).count(), 0)
        profit = self.client.get("/api/v1/finance/statements/profit-loss")
        self.assertEqual(profit.data["netProfit"], "0.00")
        draft.status = "Approved"
        draft.save(update_fields=["status"])
        self.assertEqual(JournalEntry.objects.filter(
            company=self.company, source_key__startswith=root,
        ).count(), 1)
        self.assertEqual(self.client.get(
            "/api/v1/finance/statements/profit-loss",
        ).data["netProfit"], "-240.00")
        draft.status = "Cancelled"
        draft.save(update_fields=["status"])
        self.assertEqual(JournalEntry.objects.filter(
            company=self.company, source_key__startswith=root,
        ).count(), 2)
        self.assertEqual(self.client.get(
            "/api/v1/finance/statements/profit-loss",
        ).data["netProfit"], "0.00")
        self.assertEqual(WorkshopAccountEntry.objects.count(), 0)

    def test_historical_ledger_backfill_is_explicit_and_idempotent(self):
        from io import StringIO
        from django.core.management import call_command
        from apps.finance.views import account_balance

        # bulk_create intentionally models pre-Part-14 cash entries that
        # have no automatic accounting journal projection.
        cash_entry = WorkshopAccountEntry(
            company=self.company, account=self.bank,
            amount=Decimal("25.00"), entry_type="manual_adjustment",
            source_key="historical-part14-opening",
            posted_at=timezone.now(), created_by=self.owner,
        )
        WorkshopAccountEntry.objects.bulk_create([cash_entry])
        journal_key = "cash:" + str(cash_entry.pk)
        self.assertFalse(JournalEntry.objects.filter(
            company=self.company, source_key=journal_key,
        ).exists())
        current = account_balance(self.bank)
        count = JournalEntry.objects.filter(company=self.company).count()

        dry = StringIO()
        call_command("backfill_finance_journals", company=str(self.company.pk), stdout=dry)
        self.assertIn('"mode": "dry-run"', dry.getvalue())
        self.assertEqual(JournalEntry.objects.filter(company=self.company).count(), count)

        first = StringIO()
        call_command(
            "backfill_finance_journals", company=str(self.company.pk),
            apply=True, stdout=first,
        )
        self.assertIn('"createdJournals": 1', first.getvalue())
        self.assertTrue(JournalEntry.objects.filter(
            company=self.company, source_key=journal_key,
        ).exists())
        self.assertEqual(JournalEntry.objects.filter(company=self.company).count(), count + 1)
        again = StringIO()
        call_command(
            "backfill_finance_journals", company=str(self.company.pk),
            apply=True, stdout=again,
        )
        self.assertIn('"createdJournals": 0', again.getvalue())
        self.assertEqual(account_balance(self.bank), current)
        self.assertEqual(WorkshopAccountEntry.objects.count(), 1)
