"""Reports workspace must never disclose another workshop's finance data."""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from rest_framework.exceptions import ValidationError

from apps.accounts.models import User
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.expenses.models import Expense
from apps.invoices.models import Invoice
from apps.inventory.models import StockItem
from apps.payments.models import Payment
from apps.roles.models import Role
from apps.reports.workspace import ReportsWorkspaceView
from apps.reports.fiscal_comparison import fiscal_year, get_fiscal_comparison


class ReportsWorkspaceTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Workshop Alpha", slug="report-alpha")
        role = Role.objects.create(company=self.company, name="Owner", code="OWNER", permissions=["*"])
        self.owner = User.objects.create(
            company=self.company, name="Owner", email="report-owner@example.test",
            role=role, is_active=True,
        )
        self.company.owner = self.owner
        self.company.save(update_fields=["owner"])
        self.customer = Customer.objects.create(
            company=self.company, name="Customer", phone="9876543210",
        )
        self.invoice = Invoice.objects.create(
            company=self.company, customer=self.customer, kind="invoice",
            number="INV-2026-001", date=date(2026, 11, 8), status="Finalized",
            total="500.00", paid="200.00", balance="300.00",
        )
        self.payment = Payment.objects.create(
            company=self.company, customer=self.customer, invoice=self.invoice,
            date=date(2026, 11, 9), amount="200.00", status="Completed",
        )
        self.expense = Expense.objects.create(
            company=self.company, date=date(2026, 11, 9),
            category="Utilities", description="Electricity", amount="80.00",
            status="Unpaid",
        )
        StockItem.objects.create(
            company=self.company, name="Oil filter", sku="TEST-OF-1",
            on_hand="5", cost_price="100",
        )

    def report(self, params=None, permission=None):
        request = SimpleNamespace(user=self.owner, query_params=params or {})
        allowed = (lambda user, code: code != permission) if permission else (lambda user, code: True)
        with patch("apps.reports.workspace.user_has_permission", side_effect=allowed):
            return ReportsWorkspaceView().get(request).data

    def test_live_lists_have_correct_company_and_financial_shapes(self):
        data = self.report({"from": "2026-11-01", "to": "2026-11-30"})
        self.assertEqual(data["company"]["name"], "Workshop Alpha")
        self.assertEqual(data["invoices"][0]["total"], Decimal("500.00"))
        self.assertEqual(data["payments"][0]["amount"], Decimal("200.00"))
        self.assertEqual(data["expenses"][0]["amount"], Decimal("80.00"))
        self.assertEqual(data["expenses"][0]["paidAmount"], 0)
        self.assertEqual(data["stock"][0]["onHand"], 5)
        for source in ("invoices", "payments", "expenses", "stock"):
            self.assertTrue(data["sources"][source]["available"])

    def test_missing_expense_permission_does_not_leak_amounts(self):
        data = self.report(permission="expenses.view")
        self.assertFalse(data["sources"]["expenses"]["available"])
        self.assertEqual(data["sources"]["expenses"]["reason"], "permission")
        self.assertEqual(data["expenses"], [])
        self.assertTrue(data["sources"]["payments"]["available"])

    def test_date_range_filters_finance_but_not_stock_snapshot(self):
        data = self.report({"from": "2026-10-01", "to": "2026-10-31"})
        self.assertEqual(data["invoices"], [])
        self.assertEqual(data["payments"], [])
        self.assertEqual(data["expenses"], [])
        self.assertEqual(len(data["stock"]), 1)

    def test_tenant_isolation_for_expenses(self):
        other = Company.objects.create(name="Other Garage", slug="report-other")
        Expense.objects.create(
            company=other, category="Travel", date=date(2026, 11, 9),
            amount="999999.00", description="Secret Expense",
        )
        data = self.report()
        self.assertEqual(len(data["expenses"]), 1)
        self.assertEqual(data["expenses"][0]["title"], "Electricity")

    def test_invalid_date_range_is_rejected(self):
        request = SimpleNamespace(
            user=self.owner,
            query_params={"from": "2026-11-30", "to": "2026-11-01"},
        )
        with self.assertRaises(ValidationError):
            ReportsWorkspaceView().get(request)

    def test_quarters_follow_indian_april_march_financial_year(self):
        self.assertEqual(fiscal_year(date(2026, 3, 31)), 2025)
        self.assertEqual(fiscal_year(date(2026, 4, 1)), 2026)
        data = self.report({"from": "2026-11-01", "to": "2026-11-30", "fiscalYear": "2026"})
        comparison = data["fiscalComparison"]
        self.assertEqual(comparison["currentLabel"], "FY 2026–27")
        self.assertEqual(comparison["previousLabel"], "FY 2025–26")
        self.assertEqual([q["quarter"] for q in comparison["quarters"]], ["Q1", "Q2", "Q3", "Q4"])
        self.assertEqual(comparison["quarters"][2]["current"], Decimal("500.00"))
        self.assertEqual(comparison["quarters"][2]["previous"], Decimal("0.00"))
        self.assertIsNone(comparison["quarters"][2]["changePct"])

    def test_previous_year_and_payment_channels_never_invent_figures(self):
        Invoice.objects.create(
            company=self.company, customer=self.customer,
            kind="invoice", status="Finalized", number="PREV-1",
            date=date(2025, 11, 8), total="400.00",
        )
        Payment.objects.create(
            company=self.company, customer=self.customer,
            date=date(2026, 11, 10), amount="50.00",
            method="UPI", direction="OUT", status="Completed",
        )
        comparison = get_fiscal_comparison(self.owner, 2026)
        self.assertEqual(comparison["totals"]["invoices"]["previous"], Decimal("400.00"))
        self.assertEqual(comparison["totals"]["invoices"]["current"], Decimal("500.00"))
        self.assertEqual(comparison["totals"]["invoices"]["changePct"], Decimal("25.00"))
        self.assertEqual(comparison["totals"]["payments"]["current"], Decimal("150.00"))
        channels = {row["channel"]: row for row in comparison["channels"]}
        self.assertEqual(channels["Cash"]["current"], Decimal("200.00"))
        self.assertEqual(channels["UPI"]["current"], Decimal("-50.00"))

    def test_annual_sales_permission_is_checked_separately(self):
        with patch(
            "apps.reports.fiscal_comparison.user_has_permission",
            side_effect=lambda user, code: code != "invoices.view",
        ):
            comparison = get_fiscal_comparison(self.owner, 2026)
        self.assertFalse(comparison["available"]["invoices"])
        self.assertEqual(comparison["quarters"], [])
        self.assertNotIn("invoices", comparison["totals"])

    def test_fiscal_year_query_rejects_invalid_values(self):
        request = SimpleNamespace(
            user=self.owner,
            query_params={"fiscalYear": "2026/27"},
        )
        with self.assertRaises(ValidationError):
            ReportsWorkspaceView().get(request)
