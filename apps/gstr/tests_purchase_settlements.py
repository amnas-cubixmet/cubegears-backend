"""GSTR purchase finance cross-reference: permission, period and no posting."""
from datetime import date
from decimal import Decimal

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.inventory.models import (
    Supplier, SupplierBill, SupplierPayment, SupplierPaymentAllocation,
    SupplierDebitNote, WorkshopFinancialAccount, WorkshopAccountEntry, StockMovement,
)
from apps.roles.models import Role


@override_settings(
    CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
    CREDIT_LABEL_ONLY_ACCESS_ENABLED=False,
)
class GSTRPurchaseSettlementsTests(TestCase):
    endpoint = "/api/v1/gstr/purchase-settlements?period=2026-11"

    def setUp(self):
        self.company = Company.objects.create(
            name="Purchase GST Garage", slug="purchase-gst-garage",
            gstin="32ABCDE1234F1Z5",
        )
        self.branch = Branch.objects.create(
            company=self.company, name="Main", code="GSTPS", is_active=True,
        )
        owner_role = Role.objects.create(
            company=self.company, name="Owner", code="GSTPS_OWNER",
            permissions=["*"], is_active=True, is_system=True,
        )
        self.owner = User.objects.create_user(
            email="purchase-gstr-owner@example.test", password="Strong12345",
            name="Owner", company=self.company, branch=self.branch, role=owner_role,
        )
        self.company.owner = self.owner
        self.company.save(update_fields=["owner"])
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)
        self.supplier = Supplier.objects.create(
            company=self.company, branch=self.branch,
            name="Parts Depot", gstin="32ABCDE6789G1Z5",
        )
        self.bill = SupplierBill.objects.create(
            company=self.company, branch=self.branch,
            supplier=self.supplier, invoice_number="GSTPUR-101",
            invoice_date=date(2026, 11, 10), status="Posted",
            receive_into_stock=True, subtotal=Decimal("1000.00"),
            tax=Decimal("180.00"), total=Decimal("1180.00"),
            items=[{
                "description": "Brake pad", "quantity": "1.00",
                "purchaseRate": "1000.00", "subtotal": "1000.00",
                "gstRate": "18", "tax": "180.00", "cgst": "90.00",
                "sgst": "90.00", "utgst": "0.00", "igst": "0.00",
                "taxMode": "cgst_sgst", "taxValidationStatus": "valid",
            }],
        )
        self.cash = WorkshopFinancialAccount.objects.create(
            company=self.company, branch=self.branch, name="Cash in Hand",
            kind="Cash", opening_balance=Decimal("5000.00"),
        )
        self.bank = WorkshopFinancialAccount.objects.create(
            company=self.company, branch=self.branch, name="Current Bank",
            kind="Bank", opening_balance=Decimal("10000.00"),
        )

    def add_allocation(self, account, value, key, *, kind="Payment"):
        payment = SupplierPayment.objects.create(
            company=self.company, branch=self.branch,
            supplier=self.supplier, account=account, amount=Decimal(value),
            kind=kind, method="Cash" if account.kind == "Cash" else "Bank",
            paid_on=date(2026, 11, 11), idempotency_key=key,
        )
        SupplierPaymentAllocation.objects.create(
            company=self.company, branch=self.branch,
            bill=self.bill, payment=payment, amount=Decimal(value),
        )

    def test_cash_bank_part_payment_credit_and_gst_are_one_read_only_view(self):
        self.add_allocation(self.cash, "100.00", "gstr-cash-allocated-1")
        self.add_allocation(self.bank, "400.00", "gstr-bank-allocated-1")
        SupplierDebitNote.objects.create(
            company=self.company, branch=self.branch,
            supplier=self.supplier, bill=self.bill,
            number="RETURN-101", date=date(2026, 11, 11),
            reason="Supplier agreed partial return", lines=[],
            subtotal=Decimal("80.00"), tax=Decimal("0.00"),
            total=Decimal("80.00"), payable_reduction=Decimal("80.00"),
            credit_amount=Decimal("0.00"), request_key="gstr-debit-note-101",
        )
        reply = self.client.get(self.endpoint)
        self.assertEqual(reply.status_code, 200, getattr(reply, "data", None))
        self.assertEqual(len(reply.data["rows"]), 1)
        row = reply.data["rows"][0]
        self.assertEqual(row["invoiceNumber"], "GSTPUR-101")
        self.assertEqual(row["purchaseGST"], "180.00")
        self.assertEqual(row["purchaseTotal"], "1180.00")
        self.assertEqual(row["allocatedCash"], "100.00")
        self.assertEqual(row["allocatedBank"], "400.00")
        self.assertEqual(row["paidAllocated"], "500.00")
        self.assertEqual(row["creditsApplied"], "80.00")
        self.assertEqual(row["remainingCredit"], "600.00")
        self.assertTrue(row["receivedIntoStock"])
        self.assertNotIn("recordedBalance", row)
        self.assertNotIn("accountNumber", row)
        self.assertEqual(WorkshopAccountEntry.objects.count(), 0)
        self.assertEqual(StockMovement.objects.count(), 0)
        self.assertEqual(SupplierBill.objects.count(), 1)
        # RolePermission rejects an unsupported write before Django dispatches
        # the view method (403), guaranteeing this GST readback cannot mutate.
        self.assertEqual(self.client.post(self.endpoint, {}, format="json").status_code, 403)
        self.assertEqual(SupplierBill.objects.count(), 1)

    def test_advance_application_classifies_account_without_moving_money_again(self):
        self.add_allocation(self.bank, "200.00", "gstr-bank-advance-1", kind="Advance")
        reply = self.client.get(self.endpoint)
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(reply.data["rows"][0]["allocatedBank"], "200.00")
        self.assertEqual(reply.data["rows"][0]["remainingCredit"], "980.00")
        self.assertEqual(WorkshopAccountEntry.objects.count(), 0)

    def test_only_posted_bills_in_requested_period_and_tenant_are_visible(self):
        SupplierBill.objects.create(
            company=self.company, branch=self.branch,
            supplier=self.supplier, invoice_number="DRAFT-101",
            invoice_date=date(2026, 11, 12), status="Draft",
            subtotal=Decimal("100"), tax=Decimal("18"), total=Decimal("118"),
        )
        another_company = Company.objects.create(
            name="Different Garage", slug="another-gstr-company",
        )
        other_supplier = Supplier.objects.create(
            company=another_company, name="Other Supplier",
        )
        SupplierBill.objects.create(
            company=another_company, supplier=other_supplier,
            invoice_number="PRIVATE-101", invoice_date=date(2026, 11, 10),
            status="Posted", subtotal=Decimal("100"), tax=Decimal("18"),
            total=Decimal("118"),
        )
        reply = self.client.get(self.endpoint)
        self.assertEqual([x["invoiceNumber"] for x in reply.data["rows"]], ["GSTPUR-101"])
        empty = self.client.get("/api/v1/gstr/purchase-settlements?period=2026-10")
        self.assertEqual(empty.data["rows"], [])

    def test_gstr_only_and_stock_only_staff_cannot_read_payment_allocations(self):
        staff_role = Role.objects.create(
            company=self.company, name="GST reviewer", code="GSTPS_REVIEWER",
            permissions=["gstr.view", "stock.view"],
            is_active=True,
        )
        staff = User.objects.create_user(
            email="purchase-gstr-staff@example.test", password="Strong12345",
            name="GST reviewer", company=self.company,
            branch=self.branch, role=staff_role,
        )
        self.client.force_authenticate(user=staff)
        denied = self.client.get(self.endpoint)
        self.assertEqual(denied.status_code, 403)
        staff_role.permissions = ["gstr.view", "stock.view", "payments.view"]
        staff_role.save(update_fields=["permissions"])
        allowed = self.client.get(self.endpoint)
        self.assertEqual(allowed.status_code, 200, getattr(allowed, "data", None))
