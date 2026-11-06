"""Payments homepage reconciles recorded movements and estimated accrual profit."""
from decimal import Decimal
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.expenses.models import Expense
from apps.invoices.models import Invoice, InvoiceItem
from apps.inventory.models import WorkshopFinancialAccount, WorkshopAccountEntry
from apps.roles.models import Role


@override_settings(
    CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
    CREDIT_LABEL_ONLY_ACCESS_ENABLED=False,
)
class PaymentsHomeOverviewTests(TestCase):
    url = "/api/v1/finance/payments-overview"

    def setUp(self):
        self.company = Company.objects.create(name="Ledger Home Garage", slug="ledger-home-garage")
        owner_role = Role.objects.create(
            company=self.company, name="Finance Owner", code="FIN_OWNER",
            is_system=True, permissions=["*"],
        )
        self.owner = User.objects.create_user(
            email="ledger-home@example.test", password="sample-test-only",
            name="Finance Owner", company=self.company, role=owner_role,
        )
        self.company.owner = self.owner
        self.company.save(update_fields=["owner"])
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)
        self.cash = WorkshopFinancialAccount.objects.create(
            company=self.company, name="Cash Desk", kind="Cash",
            opening_balance=Decimal("1000.00"), created_by=self.owner,
        )
        self.bank = WorkshopFinancialAccount.objects.create(
            company=self.company, name="Workshop SBI", kind="Bank",
            opening_balance=Decimal("500.00"), created_by=self.owner,
        )
        self.day = timezone.localdate()
        self.customer = Customer.objects.create(
            company=self.company, name="Test Customer", phone="+919876540000",
        )
        self.invoice = Invoice.objects.create(
            company=self.company, customer=self.customer,
            number="FINHOME-1500", kind="invoice", status="Finalized",
            date=self.day, total=Decimal("1500.00"),
            balance=Decimal("800.00"), paid=Decimal("700.00"),
        )
        InvoiceItem.objects.create(
            invoice=self.invoice, description="Paint",
            quantity=Decimal("1.00"), rate=Decimal("300.00"),
            purchase_price=Decimal("200.00"),
        )
        Expense.objects.create(
            company=self.company, date=self.day, category="Workshop Rent",
            status="Unpaid", amount=Decimal("100.00"), created_by=self.owner,
        )
        self.add_entry(self.cash, "customer_payment", Decimal("700.00"), "INV-1500")
        self.add_entry(self.cash, "Supplier Payment", Decimal("-100.00"), "Paint supplier")
        self.add_entry(self.cash, "transfer_out", Decimal("-200.00"), "To SBI")
        self.add_entry(self.bank, "transfer_in", Decimal("200.00"), "From Cash")
        self.add_entry(self.bank, "expense_payment", Decimal("-50.00"), "Rent")

    def add_entry(self, account, kind, amount, reference):
        return WorkshopAccountEntry.objects.create(
            company=self.company, account=account, entry_type=kind,
            amount=amount, source_key=kind + "_" + reference.replace(" ", "_"),
            posted_at=timezone.now(), reference=reference, created_by=self.owner,
        )

    def test_balances_daily_cash_flow_and_estimated_profit(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, getattr(response, "data", None))
        data = response.data
        self.assertEqual(data["date"], self.day.isoformat())
        self.assertEqual(data["todayReceived"], "700.00")
        self.assertEqual(data["todayPaid"], "150.00")
        self.assertEqual(data["todayNetCashFlow"], "550.00")
        self.assertEqual(data["cashBalance"], "1400.00")
        self.assertEqual(data["bankBalance"], "650.00")
        self.assertEqual(data["totalBookBalance"], "2050.00")
        result = data["performance"]
        self.assertEqual(result["revenue"], "1500.00")
        self.assertEqual(result["operatingExpenses"], "100.00")
        self.assertEqual(result["costOfPartsSold"], "200.00")
        self.assertEqual(result["totalKnownExpenses"], "300.00")
        self.assertEqual(result["estimatedProfit"], "1200.00")
        self.assertTrue(result["isEstimate"])

    def test_different_day_does_not_change_book_balances(self):
        yesterday = self.day - timedelta(days=1)
        response = self.client.get(self.url, {"date": yesterday.isoformat()})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["todayReceived"], "0.00")
        self.assertEqual(response.data["todayPaid"], "0.00")
        self.assertEqual(response.data["cashBalance"], "1400.00")
        self.assertEqual(response.data["performance"]["revenue"], "1500.00")
        bad = self.client.get(self.url, {"date": "this-morning"})
        self.assertEqual(bad.status_code, 400)

    def test_supplier_credit_is_not_new_spending_and_other_companies_excluded(self):
        Expense.objects.create(
            company=self.company, date=self.day, status="Unpaid",
            category="Supplier Payments", amount=Decimal("300.00"),
            created_by=self.owner,
        )
        other = Company.objects.create(name="Another Shop", slug="another-ledger-home-shop")
        account = WorkshopFinancialAccount.objects.create(
            company=other, name="Other Bank", kind="Bank",
            opening_balance=Decimal("99999.00"),
        )
        self.add_entry(account, "customer_payment", Decimal("5000.00"), "Other company")
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["todayReceived"], "700.00")
        self.assertEqual(response.data["bankBalance"], "650.00")
        self.assertEqual(response.data["performance"]["operatingExpenses"], "100.00")

    def test_adjustment_is_logged_without_becoming_revenue_or_profit(self):
        response = self.client.post(
            "/api/v1/finance/accounts/" + str(self.bank.pk) + "/adjust",
            {"amount": "25.00", "direction": "credit", "reason": "Counted opening change"},
            format="json",
        )
        self.assertEqual(response.status_code, 201, getattr(response, "data", None))
        home = self.client.get(self.url).data
        self.assertEqual(home["bankBalance"], "675.00")
        self.assertEqual(home["todayReceived"], "700.00")
        self.assertEqual(home["performance"]["estimatedProfit"], "1200.00")
