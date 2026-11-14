"""Automatic Finance source for supplier settlements (no account picker)."""
from decimal import Decimal

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.inventory.models import (
    Supplier, SupplierBill, WorkshopFinancialAccount, WorkshopAccountEntry,
)
from apps.roles.models import Role


@override_settings(
    CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
    CREDIT_LABEL_ONLY_ACCESS_ENABLED=False,
)
class SupplierAutoDebitTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Automatic Payment Garage", slug="automatic-payment-garage",
        )
        self.branch = Branch.objects.create(
            company=self.company, name="Main", code="MAIN", is_active=True,
        )
        role = Role.objects.create(
            company=self.company, name="Owner", code="AUTO_FINANCE_OWNER",
            permissions=["*"], is_active=True, is_system=True,
        )
        self.user = User.objects.create_user(
            email="auto-pay-owner@example.test", password="unrelated-test-password",
            name="Owner", phone="+919988887777", company=self.company,
            branch=self.branch, role=role,
        )
        self.company.owner = self.user
        self.company.save(update_fields=["owner"])
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.supplier = Supplier.objects.create(
            company=self.company, branch=self.branch, name="Automatic Parts",
        )
        self.bill = SupplierBill.objects.create(
            company=self.company, branch=self.branch, supplier=self.supplier,
            invoice_number="AUTO-SUP-1", invoice_date="2026-11-14",
            total=Decimal("1200.00"), subtotal=Decimal("1200.00"),
            status="Posted", created_by=self.user,
        )
        self.cash = WorkshopFinancialAccount.objects.create(
            company=self.company, branch=self.branch, name="Cash in Hand",
            kind="Cash", opening_balance=Decimal("500.00"), created_by=self.user,
        )
        self.bank = WorkshopFinancialAccount.objects.create(
            company=self.company, branch=self.branch, name="SBI Current",
            kind="Bank", opening_balance=Decimal("1000.00"), created_by=self.user,
        )

    def pay(self, channel, amount="100.00", key="auto-pay-0001"):
        return self.client.post("/api/v1/inventory/procurement/payments", {
            "supplierId": str(self.supplier.id), "amount": amount,
            "method": channel, "kind": "Payment", "paidOn": "2026-11-14",
            "idempotencyKey": key,
            "allocations": [{"billId": str(self.bill.id), "amount": amount}],
        }, format="json")

    def test_bank_payment_deducts_from_finance_balance_without_account_id(self):
        result = self.pay("Bank")
        self.assertEqual(result.status_code, 201, getattr(result, "data", None))
        self.assertEqual(WorkshopAccountEntry.objects.filter(
            company=self.company, account=self.bank, amount=Decimal("-100.00"),
        ).count(), 1)
        account = self.client.get(f"/api/v1/finance/accounts/{self.bank.id}")
        self.assertEqual(account.status_code, 200, getattr(account, "data", None))
        self.assertEqual(Decimal(account.data["currentBalance"]), Decimal("900.00"))
        dashboard = self.client.get("/api/v1/finance/dashboard")
        self.assertEqual(dashboard.status_code, 200, getattr(dashboard, "data", None))
        self.assertEqual(Decimal(dashboard.data["bankBalance"]), Decimal("900.00"))

    def test_cash_payment_deducts_from_cash_not_bank(self):
        result = self.pay("Cash")
        self.assertEqual(result.status_code, 201, getattr(result, "data", None))
        self.assertEqual(WorkshopAccountEntry.objects.filter(account=self.cash).count(), 1)
        self.assertEqual(WorkshopAccountEntry.objects.filter(account=self.bank).count(), 0)

    def test_multiple_banks_without_default_are_rejected_without_debit(self):
        WorkshopFinancialAccount.objects.create(
            company=self.company, branch=self.branch, name="HDFC",
            kind="Bank", opening_balance=Decimal("500.00"),
        )
        result = self.pay("Bank")
        self.assertEqual(result.status_code, 400, getattr(result, "data", None))
        self.assertIn("Default Payment Account", str(result.data))
        self.assertFalse(WorkshopAccountEntry.objects.exists())

    def test_setting_default_bank_routes_future_payments(self):
        other = WorkshopFinancialAccount.objects.create(
            company=self.company, branch=self.branch, name="HDFC",
            kind="Bank", opening_balance=Decimal("500.00"),
        )
        selected = self.client.patch(
            f"/api/v1/finance/accounts/{other.id}",
            {"isDefaultPayment": True}, format="json",
        )
        self.assertEqual(selected.status_code, 200, getattr(selected, "data", None))
        result = self.pay("Bank")
        self.assertEqual(result.status_code, 201, getattr(result, "data", None))
        self.assertEqual(WorkshopAccountEntry.objects.filter(account=other).count(), 1)
        self.assertEqual(WorkshopAccountEntry.objects.filter(account=self.bank).count(), 0)

    def test_upi_payment_debits_bank_channel_and_switching_default_clears_old(self):
        upi = WorkshopFinancialAccount.objects.create(
            company=self.company, branch=self.branch, name="PhonePe Account",
            kind="UPI", opening_balance=Decimal("300.00"),
        )
        self.assertEqual(self.client.patch(
            f"/api/v1/finance/accounts/{self.bank.id}",
            {"isDefaultPayment": True}, format="json",
        ).status_code, 200)
        self.assertEqual(self.client.patch(
            f"/api/v1/finance/accounts/{upi.id}",
            {"isDefaultPayment": True}, format="json",
        ).status_code, 200)
        self.bank.refresh_from_db()
        self.assertFalse(self.bank.is_default_payment)
        self.assertEqual(self.pay("Bank").status_code, 201)
        self.assertEqual(WorkshopAccountEntry.objects.filter(account=upi).count(), 1)

    def test_duplicate_request_does_not_reduce_balance_twice(self):
        first = self.pay("Bank")
        self.assertEqual(first.status_code, 201, getattr(first, "data", None))
        duplicate = self.pay("Bank")
        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(WorkshopAccountEntry.objects.filter(account=self.bank).count(), 1)
