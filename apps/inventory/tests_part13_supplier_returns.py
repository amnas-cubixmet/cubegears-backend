"""Part 13: posted returns, supplier debit credits, refunds and aging integrity."""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.inventory.models import (
    StockItem, StockMovement, Supplier, SupplierBill, SupplierDebitApplication,
    SupplierDebitNote, SupplierRefund, SupplierPayment, WorkshopAccountEntry,
)
from apps.roles.models import Role


@override_settings(
    CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
    CREDIT_LABEL_ONLY_ACCESS_ENABLED=False,
)
class SupplierPart13Tests(TestCase):
    root = "/api/v1/inventory/procurement"

    def setUp(self):
        self.company = Company.objects.create(name="Return Garage", slug="part13-return")
        self.branch = Branch.objects.create(
            company=self.company, name="Main", code="MAIN", is_active=True,
        )
        role = Role.objects.create(
            company=self.company, name="Owner", code="PART13_OWNER",
            permissions=["*"], is_active=True, is_system=True,
        )
        self.user = User.objects.create_user(
            email="part13@example.test", name="Owner", password="testPassword123!",
            company=self.company, branch=self.branch, role=role,
        )
        self.company.owner = self.user
        self.company.save(update_fields=["owner"])
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.vendor = Supplier.objects.create(
            company=self.company, branch=self.branch, name="Brake Supplier",
        )
        self.item = StockItem.objects.create(
            company=self.company, branch=self.branch, sku="PART13-BRAKE",
            name="Brake Fluid", on_hand=Decimal("5.00"),
            cost_price=Decimal("100.00"), selling_price=Decimal("150.00"),
        )
        result = self.client.post(f"{self.root}/accounts", {
            "name": "Bank 13", "kind": "Bank", "openingBalance": "10000.00",
        }, format="json")
        self.assertEqual(result.status_code, 201, getattr(result, "data", None))
        self.account_id = result.data["id"]
        self.serial = 0

    def bank_balance(self):
        result = self.client.get(f"{self.root}/accounts")
        self.assertEqual(result.status_code, 200)
        return next(row["recordedBalance"] for row in result.data if row["id"] == self.account_id)

    def bill(self, qty="1.00", *, receive=True, price="100.00", due=None):
        self.serial += 1
        today = timezone.localdate()
        payload = {
            "supplierId": str(self.vendor.pk),
            "invoiceNumber": f"RETURN-PO-{self.serial:04d}",
            "invoiceDate": today.isoformat(),
            "dueDate": due or (today - timedelta(days=12)).isoformat(),
            "receiveIntoStock": receive, "postNow": True,
            "items": [{
                "itemId": str(self.item.pk) if receive else None,
                "description": "Brake Fluid", "quantity": qty,
                "purchaseRate": price, "gstRate": "0",
            }],
        }
        result = self.client.post(f"{self.root}/bills", payload, format="json")
        self.assertEqual(result.status_code, 201, getattr(result, "data", None))
        return result.data

    def payment(self, bill, amount, key):
        return self.client.post(f"{self.root}/payments", {
            "supplierId": str(self.vendor.pk), "accountId": self.account_id,
            "amount": amount, "kind": "Payment", "method": "Bank Transfer",
            "idempotencyKey": key,
            "allocations": [{"billId": bill["id"], "amount": amount}],
        }, format="json")

    def debit(self, bill, qty="1.00", *, key="part13-debit-0001", number="RET-0001"):
        return self.client.post(f"{self.root}/debit-notes", {
            "billId": bill["id"], "number": number,
            "date": timezone.localdate().isoformat(),
            "reason": "Faulty goods returned to vendor",
            "requestKey": key,
            "lines": [{"lineIndex": 0, "quantity": qty}],
        }, format="json")

    def test_unpaid_return_reverses_stock_and_payable_without_cash(self):
        bill = self.bill(qty="2.00")
        self.item.refresh_from_db()
        self.assertEqual(self.item.on_hand, Decimal("7.00"))
        first = self.debit(bill, "1.00")
        self.assertEqual(first.status_code, 201, getattr(first, "data", None))
        self.assertEqual(first.data["total"], "100.00")
        self.assertEqual(first.data["payableReduction"], "100.00")
        self.assertEqual(first.data["creditAmount"], "0.00")
        self.assertEqual(self.bank_balance(), "10000.00")
        self.item.refresh_from_db()
        self.assertEqual(self.item.on_hand, Decimal("6.00"))
        self.assertEqual(StockMovement.objects.filter(
            reference=f"SUPRETURN:{first.data['id']}",
        ).count(), 1)
        self.assertEqual(self.debit(bill, "1.00").status_code, 200)
        self.assertEqual(self.debit(
            bill, "1.00", key="part13-debit-too-many", number="RET-0002",
        ).status_code, 201)  # Last purchased unit is still refundable.
        self.assertEqual(self.debit(
            bill, "1.00", key="part13-debit-excess", number="RET-0003",
        ).status_code, 400)
        self.item.refresh_from_db()
        self.assertEqual(self.item.on_hand, Decimal("5.00"))
        self.assertEqual(SupplierDebitNote.objects.count(), 2)
        self.assertEqual(SupplierBill.objects.get(pk=bill["id"]).total, Decimal("200.00"))
        summary = self.client.get(f"{self.root}/suppliers/{self.vendor.pk}/summary")
        self.assertEqual(summary.data["outstanding"], "0.00")
        self.assertEqual(summary.data["totalReturns"], "200.00")
        self.assertEqual(WorkshopAccountEntry.objects.count(), 0)

    def test_paid_return_creates_refund_credit_and_bank_refund_only_once(self):
        bill = self.bill()
        paid = self.payment(bill, "80.00", "part13-payment-80")
        self.assertEqual(paid.status_code, 201, getattr(paid, "data", None))
        self.assertEqual(self.bank_balance(), "9920.00")
        note = self.debit(bill)
        self.assertEqual(note.status_code, 201, getattr(note, "data", None))
        self.assertEqual(note.data["payableReduction"], "20.00")
        self.assertEqual(note.data["creditAmount"], "80.00")
        received = {
            "debitNoteId": note.data["id"], "accountId": self.account_id,
            "method": "Bank Transfer", "amount": "60.00",
            "reference": "REFUND-RECEIPT-1", "requestKey": "part13-refund-1",
        }
        first = self.client.post(f"{self.root}/refunds", received, format="json")
        self.assertEqual(first.status_code, 201, getattr(first, "data", None))
        self.assertEqual(first.data["creditAvailable"], "20.00")
        self.assertEqual(self.bank_balance(), "9980.00")
        second = self.client.post(f"{self.root}/refunds", received, format="json")
        self.assertEqual(second.status_code, 200, getattr(second, "data", None))
        self.assertEqual(self.bank_balance(), "9980.00")
        self.assertEqual(self.client.post(f"{self.root}/refunds", {
            **received, "amount": "21.00", "requestKey": "part13-refund-excess",
        }, format="json").status_code, 400)
        self.assertEqual(SupplierRefund.objects.count(), 1)
        self.assertEqual(WorkshopAccountEntry.objects.count(), 2)
        summary = self.client.get(f"{self.root}/suppliers/{self.vendor.pk}/summary")
        self.assertEqual(summary.data["supplierRefundReceivable"], "20.00")
        self.assertEqual(summary.data["outstanding"], "0.00")

    def test_return_credit_can_settle_another_bill_without_cash_change(self):
        original = self.bill(qty="1.00")
        self.assertEqual(self.payment(
            original, "100.00", "part13-full-payment",
        ).status_code, 201)
        note = self.debit(original)
        self.assertEqual(note.status_code, 201, getattr(note, "data", None))
        self.assertEqual(note.data["creditAmount"], "100.00")
        new = self.bill(qty="2.00", receive=False)
        amount_before = self.bank_balance()
        payload = {
            "billId": new["id"], "amount": "75.00", "requestKey": "part13-apply-credit",
        }
        applied = self.client.post(
            f"{self.root}/debit-notes/{note.data['id']}/apply", payload, format="json",
        )
        self.assertEqual(applied.status_code, 201, getattr(applied, "data", None))
        self.assertEqual(applied.data["creditAvailable"], "25.00")
        again = self.client.post(f"{self.root}/debit-notes/{note.data['id']}/apply", payload, format="json")
        self.assertEqual(again.status_code, 200, getattr(again, "data", None))
        self.assertEqual(SupplierDebitApplication.objects.count(), 1)
        self.assertEqual(self.bank_balance(), amount_before)
        bill_response = self.client.get(f"{self.root}/bills", {"supplier": str(self.vendor.pk)})
        target = next(row for row in bill_response.data if row["id"] == new["id"])
        self.assertEqual(target["creditsApplied"], "75.00")
        self.assertEqual(target["outstanding"], "125.00")
        self.assertEqual(self.client.post(f"{self.root}/payments", {
            "supplierId": str(self.vendor.pk), "accountId": self.account_id,
            "amount": "130.00", "kind": "Payment", "method": "Bank Transfer",
            "idempotencyKey": "part13-payment-overshoot",
            "allocations": [{"billId": new["id"], "amount": "130.00"}],
        }, format="json").status_code, 400)
        summary = self.client.get(f"{self.root}/suppliers/{self.vendor.pk}/summary")
        self.assertEqual(summary.data["outstanding"], "125.00")
        self.assertEqual(summary.data["supplierRefundReceivable"], "25.00")

    def test_advance_refund_does_not_reverse_applied_supplier_payment(self):
        advance = self.client.post(f"{self.root}/payments", {
            "supplierId": str(self.vendor.pk), "accountId": self.account_id,
            "amount": "400.00", "kind": "Advance", "method": "Bank Transfer",
            "idempotencyKey": "part13-advance-400",
        }, format="json")
        self.assertEqual(advance.status_code, 201, getattr(advance, "data", None))
        bill = self.bill(receive=False)
        applied = self.client.post(f"{self.root}/payments/{advance.data['id']}/allocate", {
            "billId": bill["id"], "amount": "100.00",
        }, format="json")
        self.assertEqual(applied.status_code, 201, getattr(applied, "data", None))
        refund = self.client.post(f"{self.root}/refunds", {
            "advanceId": advance.data["id"], "amount": "200.00",
            "accountId": self.account_id, "method": "Bank Transfer",
            "requestKey": "part13-advance-refund",
        }, format="json")
        self.assertEqual(refund.status_code, 201, getattr(refund, "data", None))
        self.assertEqual(refund.data["creditAvailable"], "100.00")
        self.assertEqual(self.client.post(f"{self.root}/refunds", {
            "advanceId": advance.data["id"], "amount": "101.00",
            "accountId": self.account_id, "method": "Bank Transfer",
            "requestKey": "part13-advance-too-much",
        }, format="json").status_code, 400)
        self.assertEqual(self.bank_balance(), "9800.00")
        self.assertEqual(SupplierPayment.objects.count(), 1)
        self.assertEqual(SupplierRefund.objects.count(), 1)

    def test_stock_return_does_not_allow_unavailable_units(self):
        bill = self.bill(qty="2.00")
        # Seven in inventory, so issue six (valid), leaving one.
        issued = self.client.post("/api/v1/stock/issue", {
            "itemId": str(self.item.pk), "quantity": "6.00",
            "requestKey": "part13-six-issued",
        }, format="json")
        self.assertEqual(issued.status_code, 201, getattr(issued, "data", None))
        result = self.debit(bill, qty="2.00")
        self.assertEqual(result.status_code, 400)
        self.assertEqual(SupplierDebitNote.objects.count(), 0)
        self.item.refresh_from_db()
        self.assertEqual(self.item.on_hand, Decimal("1.00"))
        self.assertEqual(StockMovement.objects.filter(movement_type="purchase_return").count(), 0)

    def test_aging_and_same_company_isolation(self):
        bill = self.bill(receive=False)
        aging = self.client.get(f"{self.root}/payables")
        self.assertEqual(aging.status_code, 200, getattr(aging, "data", None))
        self.assertEqual(aging.data["aging"]["1-30"], "100.00")
        self.assertEqual(aging.data["totalOutstanding"], "100.00")
        foreign_company = Company.objects.create(name="Other Garage", slug="part13-other")
        foreign_supplier = Supplier.objects.create(company=foreign_company, name="Other Vendor")
        foreign_bill = SupplierBill.objects.create(
            company=foreign_company, supplier=foreign_supplier,
            invoice_number="FOREIGN", invoice_date=timezone.localdate(),
            status="Posted", items=[], subtotal=Decimal("50.00"), tax=Decimal("0"),
            total=Decimal("50.00"), posted_at=timezone.now(),
        )
        self.assertEqual(self.client.post(f"{self.root}/debit-notes", {
            "billId": str(foreign_bill.pk), "number": "FOREIGN-NOTE",
            "reason": "Foreign records", "requestKey": "part13-foreign-note",
            "lines": [{"lineIndex": 0, "quantity": "1"}],
        }, format="json").status_code, 404)
        self.assertEqual(self.client.get(f"{self.root}/payables", {
            "supplier": str(foreign_supplier.pk),
        }).status_code, 404)
        self.assertEqual(SupplierDebitNote.objects.count(), 0)
        self.assertEqual(bill["outstanding"], "100.00")

    def test_supplier_ledger_and_finance_dashboard_include_return_credits(self):
        bill = self.bill()
        debit = self.debit(bill)
        self.assertEqual(debit.status_code, 201, getattr(debit, "data", None))
        ledger = self.client.get(f"{self.root}/suppliers/{self.vendor.pk}/ledger")
        self.assertEqual(ledger.status_code, 200, getattr(ledger, "data", None))
        self.assertTrue(any(row["type"] == "Purchase Return / Debit Note" for row in ledger.data["entries"]))
        self.assertEqual(ledger.data["entries"][-1]["runningPayable"], "0.00")
        dashboard = self.client.get("/api/v1/finance/dashboard")
        self.assertEqual(dashboard.status_code, 200, getattr(dashboard, "data", None))
        self.assertEqual(dashboard.data["supplierOutstanding"], "0.00")
