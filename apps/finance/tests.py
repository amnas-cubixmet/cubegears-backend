"""Finance regression tests: real account debits/credits, reversals, isolation."""
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.companies.models import Company
from apps.expenses.models import Expense, ExpenseSettlement
from apps.finance.views import account_balance
from apps.inventory.models import WorkshopFinancialAccount, WorkshopAccountEntry
from apps.payments.models import Payment
from apps.roles.models import Role


@override_settings(
    CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
    CREDIT_LABEL_ONLY_ACCESS_ENABLED=False,
)
class FinanceAccountingTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Finance Test Garage", slug="finance-test-garage")
        owner_role = Role.objects.create(
            company=self.company, name="Owner", code="OWNER",
            is_system=True, permissions=["*"],
        )
        self.owner = User.objects.create_user(
            email="finance-owner@example.test", password="test-password-do-not-use",
            name="Owner", company=self.company, role=owner_role,
        )
        self.company.owner = self.owner
        self.company.save(update_fields=["owner"])
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)
        self.cash = WorkshopFinancialAccount.objects.create(
            company=self.company, name="Petty Cash", kind="Cash",
            opening_balance=Decimal("1000.00"), created_by=self.owner,
        )
        self.bank = WorkshopFinancialAccount.objects.create(
            company=self.company, name="Test SBI", kind="Bank",
            opening_balance=Decimal("500.00"), created_by=self.owner,
            account_number="12345678901234",
        )
        self.expense = Expense.objects.create(
            company=self.company, date="2026-11-06", amount=Decimal("300.00"),
            category="Electricity", description="Monthly electricity",
            status="Approved", created_by=self.owner,
        )

    def test_account_number_is_masked_and_unpaid_expense_has_no_debit(self):
        response = self.client.get("/api/v1/finance/accounts")
        self.assertEqual(response.status_code, 200, response.data)
        account = next(row for row in response.data if row["id"] == str(self.bank.id))
        self.assertEqual(account["accountNumber"], "••••1234")
        self.assertNotIn("12345678901234", str(response.data))
        self.assertEqual(account_balance(self.cash), Decimal("1000.00"))
        self.assertEqual(WorkshopAccountEntry.objects.count(), 0)

    def test_partial_expense_settlement_idempotency_and_reversal(self):
        url = f"/api/v1/expenses/{self.expense.pk}/settle"
        payload = {
            "account": str(self.cash.pk), "amount": "120.00",
            "method": "Cash", "idempotencyKey": "finance-expense-once",
        }
        first = self.client.post(url, payload, format="json")
        self.assertEqual(first.status_code, 201, getattr(first, "data", None))
        self.assertEqual(account_balance(self.cash), Decimal("880.00"))
        self.expense.refresh_from_db()
        self.assertEqual(self.expense.status, "Partially Paid")
        again = self.client.post(url, payload, format="json")
        self.assertEqual(again.status_code, 200, getattr(again, "data", None))
        self.assertEqual(ExpenseSettlement.objects.count(), 1)
        self.assertEqual(account_balance(self.cash), Decimal("880.00"))
        too_much = self.client.post(url, {
            **payload, "amount": "181.00", "idempotencyKey": "finance-expense-two",
        }, format="json")
        self.assertEqual(too_much.status_code, 400)
        reverse_url = f"/api/v1/expenses/{self.expense.pk}/settlements/{first.data['settlementId']}/reverse"
        self.assertEqual(self.client.post(reverse_url, {}, format="json").status_code, 400)
        reverse = self.client.post(reverse_url, {"reason": "Incorrect bill"}, format="json")
        self.assertEqual(reverse.status_code, 200, getattr(reverse, "data", None))
        self.assertEqual(account_balance(self.cash), Decimal("1000.00"))
        self.assertEqual(self.client.post(reverse_url, {"reason": "Again"}, format="json").status_code, 200)
        self.assertEqual(WorkshopAccountEntry.objects.count(), 2)

    def test_paid_expense_creation_posts_once(self):
        response = self.client.post("/api/v1/expenses", {
            "date": "2026-11-06", "category": "Rent",
            "amount": "200.00", "status": "paid",
            "account": str(self.cash.pk), "payment_method": "Cash",
            "idempotencyKey": "rent-payment-once",
        }, format="json")
        self.assertEqual(response.status_code, 201, getattr(response, "data", None))
        self.assertEqual(ExpenseSettlement.objects.count(), 1)
        self.assertEqual(account_balance(self.cash), Decimal("800.00"))
        self.assertEqual(response.data["paidAmount"], "200.00")
        self.assertEqual(self.client.delete(f"/api/v1/expenses/{response.data['id']}").status_code, 400)

    def test_transfer_is_not_income_and_does_not_change_total_assets(self):
        response = self.client.post("/api/v1/finance/transfers", {
            "fromAccountId": str(self.cash.pk), "toAccountId": str(self.bank.pk),
            "amount": "300.00", "idempotencyKey": "transfer-once",
        }, format="json")
        self.assertEqual(response.status_code, 201, getattr(response, "data", None))
        self.assertEqual(account_balance(self.cash), Decimal("700.00"))
        self.assertEqual(account_balance(self.bank), Decimal("800.00"))
        self.assertEqual(account_balance(self.cash) + account_balance(self.bank), Decimal("1500.00"))
        self.assertEqual(WorkshopAccountEntry.objects.filter(entry_type__startswith="transfer_").count(), 2)
        self.assertEqual(self.client.post("/api/v1/finance/transfers", {
            "fromAccountId": str(self.cash.pk), "toAccountId": str(self.bank.pk),
            "amount": "300.00", "idempotencyKey": "transfer-once",
        }, format="json").status_code, 400)

    def test_outgoing_payment_rejects_foreign_account_and_insufficient_funds(self):
        other = Company.objects.create(name="Other Garage", slug="other-finance-garage")
        alien = WorkshopFinancialAccount.objects.create(
            company=other, name="Other Bank", kind="Bank", opening_balance=Decimal("10000"),
        )
        base = {
            "date": "2026-11-06", "amount": "100.00",
            "direction": "OUT", "method": "Cash", "category": "Office",
            "status": "Completed", "idempotency_key": "out-test-once",
        }
        foreign = self.client.post("/api/v1/payments", {**base, "account": str(alien.pk)}, format="json")
        self.assertEqual(foreign.status_code, 400, getattr(foreign, "data", None))
        empty = self.client.post("/api/v1/payments", {
            **base, "amount": "1001.00", "account": str(self.cash.pk),
        }, format="json")
        self.assertEqual(empty.status_code, 400)
        response = self.client.post("/api/v1/payments", {
            **base, "account": str(self.cash.pk),
        }, format="json")
        self.assertEqual(response.status_code, 201, getattr(response, "data", None))
        self.assertEqual(account_balance(self.cash), Decimal("900.00"))
        repeated = self.client.post("/api/v1/payments", {
            **base, "account": str(self.cash.pk),
        }, format="json")
        self.assertEqual(repeated.status_code, 200, getattr(repeated, "data", None))
        self.assertEqual(repeated.data["id"], response.data["id"])
        self.assertEqual(account_balance(self.cash), Decimal("900.00"))
        self.assertEqual(Payment.objects.count(), 1)

    def test_incoming_payment_reversal_restores_account_balance(self):
        response = self.client.post("/api/v1/payments", {
            "date": "2026-11-06", "amount": "90.00", "direction": "IN",
            "method": "Cash", "account": str(self.cash.pk),
        }, format="json")
        self.assertEqual(response.status_code, 201, getattr(response, "data", None))
        self.assertEqual(account_balance(self.cash), Decimal("1090.00"))
        url = f"/api/v1/payments/{response.data['id']}/reverse"
        reversed_payment = self.client.post(url, {"reason": "Correction"}, format="json")
        self.assertEqual(reversed_payment.status_code, 200, getattr(reversed_payment, "data", None))
        self.assertEqual(account_balance(self.cash), Decimal("1000.00"))
        self.assertEqual(self.client.post(url, {"reason": "Again"}, format="json").status_code, 200)
        self.assertEqual(WorkshopAccountEntry.objects.count(), 2)

    def test_invalid_money_rejected_without_posting(self):
        response = self.client.post(
            f"/api/v1/finance/accounts/{self.cash.pk}/adjust",
            {"amount": "-10.00", "direction": "debit", "reason": "Test"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        invalid = self.client.post(
            f"/api/v1/expenses/{self.expense.pk}/settle",
            {"account": str(self.cash.pk), "amount": "1.999", "idempotencyKey": "bad-precision"},
            format="json",
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(account_balance(self.cash), Decimal("1000.00"))

    def test_ledger_csv_and_pdf_exports_are_scoped_and_downloadable(self):
        WorkshopAccountEntry.objects.create(
            company=self.company, account=self.cash, amount=Decimal("12.00"),
            entry_type="manual_adjustment", source_key="export-test-once",
            reference="Cash adjustment", posted_at=__import__("django.utils.timezone", fromlist=["now"]).now(),
            created_by=self.owner,
        )
        csv_response = self.client.get("/api/v1/finance/ledger?export=csv")
        self.assertEqual(csv_response.status_code, 200)
        self.assertEqual(csv_response["Content-Type"], "text/csv")
        self.assertIn(b"Cash adjustment", csv_response.content)
        pdf_response = self.client.get("/api/v1/finance/ledger?export=pdf")
        self.assertEqual(pdf_response.status_code, 200, getattr(pdf_response, "data", None))
        self.assertEqual(pdf_response["Content-Type"], "application/pdf")
        self.assertTrue(pdf_response.content.startswith(b"%PDF"))

    def test_customer_invoice_1500_receives_700_due_800_and_cash_increases_once(self):
        from apps.customers.models import Customer
        from apps.invoices.models import Invoice

        customer = Customer.objects.create(
            company=self.company, name="Customer One", phone="+919876543210",
        )
        invoice = Invoice.objects.create(
            company=self.company, customer=customer, kind="invoice",
            number="INV-WORKFLOW-1500", date="2026-11-06",
            status="Finalized", finalized_at=timezone.now(),
            total=Decimal("1500.00"), balance=Decimal("1500.00"),
        )
        payload = {
            "customer": str(customer.pk), "invoice": str(invoice.pk),
            "date": "2026-11-06", "direction": "IN",
            "amount": "700.00", "method": "Cash",
            "account": str(self.cash.pk), "status": "Completed",
            "idempotency_key": "customer-1500-invoice-700-once",
        }
        first = self.client.post("/api/v1/payments", payload, format="json")
        self.assertEqual(first.status_code, 201, getattr(first, "data", None))
        invoice.refresh_from_db()
        self.assertEqual(invoice.paid, Decimal("700.00"))
        self.assertEqual(invoice.balance, Decimal("800.00"))
        self.assertEqual(account_balance(self.cash), Decimal("1700.00"))
        repeated = self.client.post("/api/v1/payments", payload, format="json")
        self.assertEqual(repeated.status_code, 200, getattr(repeated, "data", None))
        self.assertEqual(repeated.data["id"], first.data["id"])
        self.assertEqual(account_balance(self.cash), Decimal("1700.00"))
        self.assertEqual(WorkshopAccountEntry.objects.count(), 1)
        dash = self.client.get("/api/v1/finance/dashboard")
        self.assertEqual(dash.status_code, 200, getattr(dash, "data", None))
        self.assertEqual(dash.data["totalReceived"], "700.00")
        self.assertEqual(dash.data["outstandingCustomer"], "800.00")

    def test_retrying_paid_expense_creation_does_not_add_second_liability(self):
        payload = {
            "date": "2026-11-06", "category": "Workshop Rent",
            "amount": "150.00", "status": "Paid",
            "account": str(self.cash.pk), "payment_method": "Cash",
            "idempotencyKey": "expense-rent-creation-once",
        }
        first = self.client.post("/api/v1/expenses", payload, format="json")
        self.assertEqual(first.status_code, 201, getattr(first, "data", None))
        first_count = Expense.objects.count()
        duplicate = self.client.post("/api/v1/expenses", payload, format="json")
        self.assertEqual(duplicate.status_code, 400, getattr(duplicate, "data", None))
        self.assertEqual(Expense.objects.count(), first_count)
        self.assertEqual(ExpenseSettlement.objects.count(), 1)
        self.assertEqual(WorkshopAccountEntry.objects.count(), 1)
        self.assertEqual(account_balance(self.cash), Decimal("850.00"))

    def test_unlinked_legacy_receipt_disclosed_without_creating_bank_money(self):
        Payment.objects.create(
            company=self.company, date="2026-11-06", direction="IN",
            amount=Decimal("100.00"), method="Cash", status="Completed",
            reference="OLD-CASH-RECEIPT", recorded_by=self.owner,
        )
        response = self.client.get("/api/v1/finance/payment-activity")
        self.assertEqual(response.status_code, 200, getattr(response, "data", None))
        data = response.data
        self.assertEqual(data["count"], 1)
        entry = next(row for row in data["results"] if row["reference"] == "OLD-CASH-RECEIPT")
        self.assertEqual(entry["source"], "payment")
        self.assertEqual(entry["amount"], "100.00")
        self.assertEqual(entry["account"], "")
        self.assertFalse(entry["ledgerPosted"])
        # Imported legacy receipts without financial account entries must not
        # invent cash/bank funds or silently post a financial transaction.
        self.assertEqual(WorkshopAccountEntry.objects.count(), 0)
        self.assertEqual(account_balance(self.cash), Decimal("1000.00"))
        self.assertEqual(account_balance(self.bank), Decimal("500.00"))


    def test_draft_invoice_does_not_generate_revenue_or_profit(self):
        from django.utils import timezone
        from apps.customers.models import Customer
        from apps.invoices.models import Invoice

        customer = Customer.objects.create(company=self.company, name="Draft Customer")
        Invoice.objects.create(
            company=self.company, customer=customer,
            number="DRAFT-NOT-REVENUE", kind="invoice",
            date=timezone.localdate(), status="Draft",
            total=Decimal("9000.00"), balance=Decimal("9000.00"),
        )
        report = self.client.get("/api/v1/finance/dashboard?period=all")
        self.assertEqual(report.status_code, 200)
        self.assertEqual(report.data["netSalesRevenue"], "0.00")
        self.assertEqual(report.data["recordedExpenses"], "300.00")
        self.assertEqual(report.data["estimatedProfit"], "-300.00")
        self.assertEqual(report.data["todayReceived"], "0.00")
        self.assertEqual(report.data["bankBalance"], "500.00")
        self.assertEqual(report.data["cashBalance"], "1000.00")

    def test_inactive_bank_funds_stay_in_book_balances_without_allowing_posting(self):
        self.bank.is_active = False
        self.bank.save(update_fields=["is_active"])
        report = self.client.get("/api/v1/finance/dashboard")
        self.assertEqual(report.status_code, 200)
        self.assertEqual(report.data["bankBalance"], "500.00")
        self.assertTrue(any(
            a["id"] == str(self.bank.pk) and a["isActive"] is False
            for a in report.data["accounts"]
        ))
        blocked = self.client.post("/api/v1/finance/accounts/" + str(self.bank.pk) + "/adjust", {
            "direction": "credit", "amount": "50.00", "reason": "Attempt to post to inactive account"
        }, format="json")
        self.assertEqual(blocked.status_code, 400)


    def test_finance_balance_fields_require_explicit_role_grant(self):
        role = Role.objects.create(
            company=self.company, name="Payment Clerk", code="PAYMENT_CLERK",
            permissions=["payments.view"],
        )
        clerk = User.objects.create_user(
            email="finance-clerk@example.test", password="test-password-do-not-use",
            name="Payment Clerk", company=self.company, role=role,
        )
        self.client.force_authenticate(user=clerk)

        accounts = self.client.get("/api/v1/finance/accounts")
        self.assertEqual(accounts.status_code, 200, accounts.data)
        bank = next(row for row in accounts.data if row["id"] == str(self.bank.id))
        self.assertNotIn("currentBalance", bank)
        self.assertNotIn("openingBalance", bank)

        detail = self.client.get(f"/api/v1/finance/accounts/{self.bank.pk}")
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertNotIn("currentBalance", detail.data)
        self.assertNotIn("openingBalance", detail.data)
        self.assertTrue(all("runningBalance" not in row for row in detail.data["ledger"]))

        dashboard = self.client.get("/api/v1/finance/dashboard")
        self.assertEqual(dashboard.status_code, 200, dashboard.data)
        self.assertNotIn("bankBalance", dashboard.data)
        self.assertNotIn("cashBalance", dashboard.data)

        role.permissions = ["payments.view", "finance.accounts.balance.view"]
        role.save(update_fields=["permissions"])
        granted = self.client.get("/api/v1/finance/accounts")
        self.assertEqual(granted.status_code, 200, granted.data)
        bank = next(row for row in granted.data if row["id"] == str(self.bank.id))
        self.assertIn("currentBalance", bank)
        self.assertEqual(bank["currentBalance"], "500.00")
