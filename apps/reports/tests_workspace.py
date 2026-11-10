"""Reports workspace must never disclose another workshop's finance data."""
from datetime import date
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
        self.assertEqual(data["invoices"][0]["total"], self.invoice.total)
        self.assertEqual(data["payments"][0]["amount"], self.payment.amount)
        self.assertEqual(data["expenses"][0]["amount"], self.expense.amount)
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
