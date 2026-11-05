"""Supplier credit is based on one received PO and exactly one Expense per payout."""
from decimal import Decimal
from uuid import uuid4

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.companies.models import Company
from apps.roles.models import Role
from apps.inventory.models import PurchaseOrder, Supplier, SupplierCredit, SupplierCreditPayment
from apps.expenses.models import Expense


@override_settings(
    CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
    CREDIT_LABEL_ONLY_ACCESS_ENABLED=False,
)
class SupplierCreditLedgerTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Main Workshop", slug="supplier-ledger-main")
        self.role = Role.objects.create(
            company=self.company, name="Owner", code="OWNER_TEST",
            permissions=["stock.view", "stock.edit", "expenses.view", "expenses.create"],
            is_active=True,
        )
        self.owner = User.objects.create_user(
            email="supplier-owner@example.test", password="safe-test-pass",
            name="Owner", company=self.company, role=self.role,
        )
        self.company.owner = self.owner
        self.company.save(update_fields=["owner"])
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.supplier = Supplier.objects.create(
            company=self.company, name="Original Parts Supplier",
        )
        self.po = PurchaseOrder.objects.create(
            company=self.company, number="PO-4001", supplier=self.supplier,
            status="Received", total=Decimal("1000.00"),
        )

    def create_credit(self, purchase_order=None):
        return self.client.post(
            "/api/v1/stock/supplier-credits",
            {"purchaseOrder": str(purchase_order or self.po.pk),
             "dueDate": "2026-12-31"},
            format="json",
        )

    def record(self, credit, amount, key=None, method="Cash", reference=""):
        return self.client.post(
            "/api/v1/stock/supplier-payments",
            {"credit": str(credit), "amount": amount, "method": method,
             "reference": reference, "idempotencyKey": str(key or uuid4())},
            format="json",
        )

    def test_credit_reuses_existing_supplier_and_purchase(self):
        created = self.create_credit()
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(created.data["purchaseNumber"], "PO-4001")
        self.assertEqual(created.data["total"], "1000.00")
        self.assertEqual(created.data["outstanding"], "1000.00")
        self.assertEqual(self.create_credit().status_code, 400)
        self.assertEqual(Supplier.objects.count(), 1)
        self.assertEqual(PurchaseOrder.objects.count(), 1)
        self.assertEqual(SupplierCredit.objects.count(), 1)

    def test_partial_and_final_payment_create_one_expense_each(self):
        credit = self.create_credit().data["id"]
        first = self.record(credit, "350.00", method="Bank", reference="BANK-001")
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(first.data["amount"], "350.00")
        view = self.client.get("/api/v1/stock/supplier-credits")
        self.assertEqual(view.status_code, 200)
        self.assertEqual(view.data[0]["outstanding"], "650.00")
        self.assertEqual(view.data[0]["status"], "Partially Paid")
        second = self.record(credit, "650.00", method="Cash")
        self.assertEqual(second.status_code, 201, second.data)
        self.assertEqual(self.client.get("/api/v1/stock/supplier-credits").data[0]["status"], "Settled")
        self.assertEqual(Expense.objects.filter(category="Supplier Credit Settlement").count(), 2)
        self.assertEqual(SupplierCreditPayment.objects.count(), 2)

    def test_retries_never_duplicate_expense_or_payment(self):
        credit = self.create_credit().data["id"]
        request_id = uuid4()
        a = self.record(credit, "225.00", key=request_id)
        b = self.record(credit, "225.00", key=request_id)
        self.assertEqual(a.status_code, 201, a.data)
        self.assertEqual(b.status_code, 200, b.data)
        self.assertEqual(a.data["expenseId"], b.data["expenseId"])
        self.assertEqual(Expense.objects.count(), 1)
        self.assertEqual(SupplierCreditPayment.objects.count(), 1)
        self.assertEqual(self.record(credit, "226.00", key=request_id).status_code, 400)

    def test_invalid_overpayment_and_missing_bank_reference_are_rejected(self):
        credit = self.create_credit().data["id"]
        self.assertEqual(self.record(credit, "1000.01").status_code, 400)
        self.assertEqual(self.record(credit, "0").status_code, 400)
        self.assertEqual(self.record(credit, "NaN").status_code, 400)
        self.assertEqual(self.record(credit, "20", method="UPI").status_code, 400)
        self.assertEqual(Expense.objects.count(), 0)

    def test_not_received_po_cannot_become_credit(self):
        self.po.status = "Ordered"
        self.po.save(update_fields=["status"])
        self.assertEqual(self.create_credit().status_code, 400)
        self.assertEqual(SupplierCredit.objects.count(), 0)

    def test_other_tenant_supplier_and_credit_cannot_be_accessed(self):
        other = Company.objects.create(name="Other Workshop", slug="supplier-ledger-other")
        other_supplier = Supplier.objects.create(company=other, name="Other Supplier")
        other_po = PurchaseOrder.objects.create(
            company=other, supplier=other_supplier, number="PO-OTHER",
            total=Decimal("80"), status="Received",
        )
        self.assertEqual(self.create_credit(other_po.pk).status_code, 400)
        own_credit = self.create_credit().data["id"]
        other_credit = SupplierCredit.objects.create(company=other, purchase_order=other_po)
        self.assertEqual(self.client.get(f"/api/v1/stock/supplier-credits/{other_credit.pk}").status_code, 404)
        self.assertEqual(self.record(other_credit.pk, "10").status_code, 400)
        self.assertEqual(self.client.get("/api/v1/stock/supplier-credits").data[0]["id"], str(own_credit))

    def test_linked_purchase_and_expense_are_immutable(self):
        credit = self.create_credit().data["id"]
        payment = self.record(credit, "50").data
        self.assertEqual(
            self.client.patch(f"/api/v1/stock/purchase-orders/{self.po.pk}",
                              {"total": "10.00"}, format="json").status_code,
            400,
        )
        self.assertEqual(
            self.client.delete(f"/api/v1/expenses/{payment['expenseId']}").status_code,
            400,
        )
        self.assertEqual(
            self.client.patch(f"/api/v1/expenses/{payment['expenseId']}",
                              {"amount": "1.00"}, format="json").status_code,
            400,
        )

    def test_staff_without_finance_permission_denied(self):
        staff_role = Role.objects.create(
            company=self.company, code="STOCK_ONLY", name="Stock Staff",
            permissions=["stock.view"], is_active=True,
        )
        staff = User.objects.create_user(
            email="supplier-staff@example.test", password="safe-test-pass",
            name="Staff", company=self.company, role=staff_role,
        )
        self.client.force_authenticate(staff)
        self.assertEqual(self.client.get("/api/v1/stock/supplier-credits").status_code, 403)
        self.assertEqual(self.client.post("/api/v1/stock/supplier-credits", {}, format="json").status_code, 403)
