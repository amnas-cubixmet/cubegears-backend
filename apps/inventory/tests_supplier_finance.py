"""Tenant-safe supplier credit accounting / single-posting regressions."""
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.inventory.models import (
    Supplier, SupplierBill, SupplierPayment, SupplierPaymentAllocation, SupplierLinePaymentAllocation,
    StockItem, StockMovement, WorkshopAccountEntry,
)
from apps.roles.models import Role


@override_settings(
    CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
    CREDIT_LABEL_ONLY_ACCESS_ENABLED=False,
)
class SupplierFinanceTests(TestCase):
    base = "/api/v1/inventory/procurement"

    def setUp(self):
        self.company = Company.objects.create(name="Finance Garage", slug="finance-garage")
        self.branch = Branch.objects.create(company=self.company, name="Main", code="M", is_active=True)
        role = Role.objects.create(
            company=self.company, name="Owner", code="SUPFIN_OWNER",
            permissions=["*"], is_active=True, is_system=True,
        )
        self.owner = User.objects.create_user(
            email="fin-owner@example.test", password="safe-password",
            name="Finance Owner", phone="+919999000001",
            company=self.company, branch=self.branch, role=role,
        )
        self.company.owner = self.owner
        self.company.save(update_fields=["owner"])
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)
        self.supplier = Supplier.objects.create(
            company=self.company, branch=self.branch, name="Paint Supplier", is_active=True,
        )
        self.stock = StockItem.objects.create(
            company=self.company, branch=self.branch, name="Paint Bottle", sku="PB-01",
            on_hand="2.00", cost_price="400.00", selling_price="550.00",
        )
        response = self.client.post(f"{self.base}/accounts", {
            "name": "Workshop Bank", "kind": "Bank", "openingBalance": "20000.00",
        }, format="json")
        self.assertEqual(response.status_code, 201, getattr(response, "data", None))
        self.account_id = response.data["id"]
        self.seq = 0

    def create_bill(self, total="10000.00", *, receive=False, sku_item=False):
        self.seq += 1
        body = {
            "supplierId": str(self.supplier.pk),
            "invoiceNumber": f"PS-{self.seq:05d}",
            "invoiceDate": "2026-11-05",
            "dueDate": "2026-11-15",
            "receiveIntoStock": receive,
            "items": [{
                "description": "Paint",
                "quantity": "1.00", "purchaseRate": total, "gstRate": "0",
                **({"itemId": str(self.stock.pk)} if sku_item else {}),
            }],
        }
        r = self.client.post(f"{self.base}/bills", body, format="json")
        self.assertEqual(r.status_code, 201, getattr(r, "data", None))
        return r.data["id"]

    def post_bill(self, pk):
        return self.client.post(f"{self.base}/bills/{pk}/post", {}, format="json")

    def pay(self, allocations=None, *, amount="4000.00", kind="Payment", key="unique-payment-a"):
        return self.client.post(f"{self.base}/payments", {
            "supplierId": str(self.supplier.pk), "accountId": self.account_id,
            "amount": amount, "kind": kind, "method": "Bank Transfer",
            "paidOn": "2026-11-05", "idempotencyKey": key,
            "allocations": allocations or [],
        }, format="json")

    def summary(self):
        r = self.client.get(f"{self.base}/suppliers/{self.supplier.pk}/summary")
        self.assertEqual(r.status_code, 200, getattr(r, "data", None))
        return r.data

    def account_balance(self):
        r = self.client.get(f"{self.base}/accounts")
        self.assertEqual(r.status_code, 200)
        return r.data[0]["recordedBalance"]

    def test_draft_creates_neither_payable_stock_movement_nor_bank_entry(self):
        bill = self.create_bill(total="2100.00", receive=True, sku_item=True)
        self.assertEqual(SupplierBill.objects.get(pk=bill).status, "Draft")
        self.assertEqual(self.summary()["outstanding"], "0.00")
        self.stock.refresh_from_db()
        self.assertEqual(str(self.stock.on_hand), "2.00")
        self.assertEqual(StockMovement.objects.filter(reference=f"SUPBILL:{bill}").count(), 0)
        self.assertEqual(WorkshopAccountEntry.objects.count(), 0)
        self.assertEqual(self.account_balance(), "20000.00")

    def test_post_creates_one_inventory_receipt_and_supplier_liability(self):
        bill = self.create_bill(total="2000.00", receive=True, sku_item=True)
        posted = self.post_bill(bill)
        self.assertEqual(posted.status_code, 200, getattr(posted, "data", None))
        self.assertEqual(posted.data["outstanding"], "2000.00")
        self.assertEqual(self.post_bill(bill).status_code, 400)
        self.stock.refresh_from_db()
        self.assertEqual(str(self.stock.on_hand), "3.00")
        moves = StockMovement.objects.filter(reference=f"SUPBILL:{bill}")
        self.assertEqual(moves.count(), 1)
        self.assertEqual(str(moves.first().unit_cost), "2000.00")
        self.assertEqual(self.summary()["outstanding"], "2000.00")
        self.assertEqual(self.account_balance(), "20000.00")

    def test_bill_price_and_tax_derive_from_line_values_not_client_total(self):
        r = self.client.post(f"{self.base}/bills", {
            "supplierId": str(self.supplier.pk), "invoiceNumber": "GST-01",
            "invoiceDate": "2026-11-05", "total": "0.00",
            "items": [{"description": "Paint", "quantity": "5", "purchaseRate": "400", "gstRate": "5"}],
        }, format="json")
        self.assertEqual(r.status_code, 201, getattr(r, "data", None))
        self.assertEqual(r.data["subtotal"], "2000.00")
        self.assertEqual(r.data["tax"], "100.00")
        self.assertEqual(r.data["total"], "2100.00")

    def test_credit_partial_and_later_supplier_payments(self):
        bill = self.create_bill()
        self.assertEqual(self.post_bill(bill).status_code, 200)
        self.assertEqual(self.summary()["outstanding"], "10000.00")
        first = self.pay([{"billId": bill, "amount": "4000.00"}])
        self.assertEqual(first.status_code, 201, getattr(first, "data", None))
        self.assertEqual(self.summary()["outstanding"], "6000.00")
        self.assertEqual(self.account_balance(), "16000.00")
        later = self.pay([{"billId": bill, "amount": "2500.00"}], amount="2500.00", key="unique-payment-b")
        self.assertEqual(later.status_code, 201, getattr(later, "data", None))
        self.assertEqual(self.summary()["outstanding"], "3500.00")
        self.assertEqual(self.account_balance(), "13500.00")
        self.assertEqual(SupplierPayment.objects.count(), 2)
        self.assertEqual(SupplierPaymentAllocation.objects.count(), 2)
        self.assertEqual(WorkshopAccountEntry.objects.count(), 2)

    def test_single_payment_allocated_across_multiple_bills(self):
        a = self.create_bill("5000.00")
        b = self.create_bill("3000.00")
        c = self.create_bill("2000.00")
        for bill in (a, b, c):
            self.assertEqual(self.post_bill(bill).status_code, 200)
        payment = self.pay([
            {"billId": a, "amount": "5000.00"},
            {"billId": b, "amount": "1000.00"},
        ], amount="6000.00", key="allocation-multiple")
        self.assertEqual(payment.status_code, 201, getattr(payment, "data", None))
        self.assertEqual(self.summary()["outstanding"], "4000.00")
        self.assertEqual(self.account_balance(), "14000.00")
        self.assertEqual(SupplierPaymentAllocation.objects.count(), 2)

    def test_advance_applied_without_second_bank_deduction(self):
        payment = self.pay(amount="3000.00", kind="Advance", key="advance-payment")
        self.assertEqual(payment.status_code, 201, getattr(payment, "data", None))
        self.assertEqual(self.account_balance(), "17000.00")
        self.assertEqual(self.summary()["availableAdvance"], "3000.00")
        bill = self.create_bill("8000.00")
        self.assertEqual(self.post_bill(bill).status_code, 200)
        applied = self.client.post(f"{self.base}/payments/{payment.data['id']}/allocate", {
            "billId": bill, "amount": "3000.00",
        }, format="json")
        self.assertEqual(applied.status_code, 201, getattr(applied, "data", None))
        self.assertEqual(self.summary()["outstanding"], "5000.00")
        self.assertEqual(self.summary()["availableAdvance"], "0.00")
        self.assertEqual(self.account_balance(), "17000.00")
        self.assertEqual(WorkshopAccountEntry.objects.count(), 1)
        self.assertEqual(self.client.post(
            f"{self.base}/payments/{payment.data['id']}/allocate",
            {"billId": bill, "amount": "500.00"}, format="json",
        ).status_code, 400)

    def test_idempotency_and_account_posting_cannot_repeat(self):
        bill = self.create_bill()
        self.post_bill(bill)
        body = [{"billId": bill, "amount": "4000.00"}]
        self.assertEqual(self.pay(body).status_code, 201)
        self.assertEqual(self.pay(body).status_code, 400)
        self.assertEqual(SupplierPayment.objects.count(), 1)
        self.assertEqual(WorkshopAccountEntry.objects.count(), 1)
        self.assertEqual(self.account_balance(), "16000.00")

    def test_reject_overallocation_and_wrong_account_method_without_money_out(self):
        bill = self.create_bill("3000.00")
        self.post_bill(bill)
        self.assertEqual(self.pay([{"billId": bill, "amount": "4000.00"}]).status_code, 400)
        bad = self.client.post(f"{self.base}/payments", {
            "supplierId": str(self.supplier.pk), "accountId": self.account_id,
            "amount": "1500.00", "kind": "Advance", "method": "Cash",
            "idempotencyKey": "wrong-account-type",
        }, format="json")
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(WorkshopAccountEntry.objects.count(), 0)

    def test_draft_cannot_be_paid_and_negative_account_balance_rejected(self):
        bill = self.create_bill()
        self.assertEqual(self.pay([{"billId": bill, "amount": "4000.00"}]).status_code, 400)
        self.assertEqual(self.pay(amount="21000.00", kind="Advance", key="large-advance").status_code, 400)
        self.assertEqual(WorkshopAccountEntry.objects.count(), 0)

    def test_duplicate_supplier_invoice_is_blocked(self):
        bill = self.create_bill()
        first = SupplierBill.objects.get(pk=bill)
        duplicate = self.client.post(f"{self.base}/bills", {
            "supplierId": str(self.supplier.pk),
            "invoiceNumber": first.invoice_number.lower(),
            "invoiceDate": "2026-11-05",
            "items": [{"description": "Paint", "quantity": 1, "purchaseRate": 500}],
        }, format="json")
        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(SupplierBill.objects.count(), 1)

    def test_tenant_isolation_on_supplier_bill_and_financial_account(self):
        foreign = Company.objects.create(name="Second Garage", slug="second-finance-garage")
        role = Role.objects.create(company=foreign, name="Owner", code="SECOND_OWNER",
                                   permissions=["*"], is_active=True)
        user = User.objects.create_user(
            email="second-fin@example.test", password="safe-password",
            name="Other Owner", company=foreign, role=role,
        )
        foreign.owner = user
        foreign.save(update_fields=["owner"])
        self.client.force_authenticate(user=user)
        self.assertEqual(self.client.get(f"{self.base}/suppliers/{self.supplier.pk}/summary").status_code, 404)
        self.assertEqual(self.client.get(f"{self.base}/accounts/{self.account_id}/ledger").status_code, 404)
        self.assertEqual(self.client.post(f"{self.base}/bills", {
            "supplierId": str(self.supplier.pk), "invoiceNumber": "FOREIGN",
            "invoiceDate": "2026-11-05",
            "items": [{"description": "Paint", "quantity": "1", "purchaseRate": "500"}],
        }, format="json").status_code, 404)

    def test_view_only_staff_cannot_post_supplier_payments(self):
        role = Role.objects.create(company=self.company, name="Finance Viewer",
                                   code="FINANCE_VIEWER",
                                   permissions=["payments.view", "stock.view"], is_active=True)
        viewer = User.objects.create_user(
            email="finance-viewer@example.test", password="safe-password",
            name="Viewer", company=self.company, branch=self.branch, role=role,
        )
        self.client.force_authenticate(user=viewer)
        self.assertEqual(self.client.get(f"{self.base}/accounts").status_code, 200)
        self.assertEqual(self.pay(amount="100.00", kind="Advance", key="viewer-payment").status_code, 403)

    def test_supplier_ledger_running_balance_matches_paid_bill(self):
        bill = self.create_bill("1000.00")
        self.post_bill(bill)
        self.assertEqual(self.pay([{"billId": bill, "amount": "250.00"}],
                                  amount="250.00", key="ledger-payment").status_code, 201)
        ledger = self.client.get(f"{self.base}/suppliers/{self.supplier.pk}/ledger")
        self.assertEqual(ledger.status_code, 200, getattr(ledger, "data", None))
        self.assertEqual(len(ledger.data["entries"]), 2)
        self.assertEqual(ledger.data["entries"][-1]["runningPayable"], "750.00")
        self.assertEqual(ledger.data["outstanding"], "750.00")

    def test_atomic_full_paid_purchase_receives_stock_once_and_debits_bank_once(self):
        posted = self.client.post(f"{self.base}/bills", {
            "supplierId": str(self.supplier.pk), "invoiceNumber": "POST-FULL-001",
            "invoiceDate": "2026-11-06", "postNow": True, "receiveIntoStock": True,
            "items": [{"itemId": str(self.stock.pk), "quantity": "2", "purchaseRate": "1000", "gstRate": "0"}],
            "payment": {
                "accountId": self.account_id, "amount": "2000.00",
                "method": "Bank Transfer", "paidOn": "2026-11-06",
                "kind": "Payment", "idempotencyKey": "post-full-001",
            },
        }, format="json")
        self.assertEqual(posted.status_code, 201, getattr(posted, "data", None))
        self.assertEqual(posted.data["postingStatus"], "Posted")
        self.assertEqual(posted.data["outstanding"], "0")
        self.assertEqual(self.summary()["outstanding"], "0.00")
        self.assertEqual(self.account_balance(), "18000.00")
        self.stock.refresh_from_db()
        self.assertEqual(str(self.stock.on_hand), "4.00")
        self.assertEqual(SupplierPayment.objects.count(), 1)
        self.assertEqual(WorkshopAccountEntry.objects.count(), 1)
        bill = SupplierBill.objects.get(invoice_number="POST-FULL-001")
        self.assertEqual(StockMovement.objects.filter(reference=f"SUPBILL:{bill.pk}").count(), 1)
        self.assertEqual(self.post_bill(bill.pk).status_code, 400)
        self.assertEqual(StockMovement.objects.filter(reference=f"SUPBILL:{bill.pk}").count(), 1)

    def test_partial_payment_by_purchase_item_reconciles_one_bill_liability(self):
        result = self.client.post(f"{self.base}/bills", {
            "supplierId": str(self.supplier.pk), "invoiceNumber": "LINE-PARTIAL-001",
            "invoiceDate": "2026-11-06", "postNow": True,
            "items": [
                {"description": "Brake Pad", "quantity": "1", "purchaseRate": "3000", "gstRate": "0"},
                {"description": "Oil Filter", "quantity": "1", "purchaseRate": "2000", "gstRate": "0"},
            ],
            "payment": {
                "accountId": self.account_id, "amount": "4000.00",
                "kind": "Payment", "method": "Bank Transfer",
                "idempotencyKey": "partial-item-payment-001",
                "allocations": [{
                    "amount": "4000.00",
                    "lineAllocations": [
                        {"lineIndex": 0, "amount": "3000.00"},
                        {"lineIndex": 1, "amount": "1000.00"},
                    ],
                }],
            },
        }, format="json")
        self.assertEqual(result.status_code, 201, getattr(result, "data", None))
        self.assertEqual(SupplierBill.objects.count(), 1)
        self.assertEqual(SupplierPayment.objects.count(), 1)
        self.assertEqual(SupplierPaymentAllocation.objects.count(), 1)
        self.assertEqual(SupplierLinePaymentAllocation.objects.count(), 2)
        self.assertEqual(WorkshopAccountEntry.objects.count(), 1)
        self.assertEqual(self.summary()["outstanding"], "1000.00")
        self.assertEqual(self.account_balance(), "16000.00")
        self.assertEqual(result.data["items"][0]["paidAllocation"], "3000.00")
        self.assertEqual(result.data["items"][1]["lineOutstanding"], "1000.00")

    def test_post_failure_rolls_back_bill_inventory_and_bank(self):
        response = self.client.post(f"{self.base}/bills", {
            "supplierId": str(self.supplier.pk), "invoiceNumber": "ROLLBACK-001",
            "invoiceDate": "2026-11-06", "postNow": True, "receiveIntoStock": True,
            "items": [{"itemId": str(self.stock.pk), "quantity": "1", "purchaseRate": "25000"}],
            "payment": {
                "accountId": self.account_id, "amount": "25000.00",
                "kind": "Payment", "method": "Bank Transfer",
                "idempotencyKey": "rollback-payment-001",
            },
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(SupplierBill.objects.count(), 0)
        self.assertEqual(SupplierPayment.objects.count(), 0)
        self.assertEqual(StockMovement.objects.count(), 0)
        self.assertEqual(WorkshopAccountEntry.objects.count(), 0)
        self.stock.refresh_from_db()
        self.assertEqual(str(self.stock.on_hand), "2.00")

    def test_purchase_gst_options_include_0_5_18_and_indian_states(self):
        result = self.client.get(f"{self.base}/gst-options")
        self.assertEqual(result.status_code, 200, getattr(result, "data", None))
        self.assertEqual([row["total"] for row in result.data["rates"]], ["0", "5", "18"])
        self.assertIn("32", [row["code"] for row in result.data["states"]])

    def test_intra_state_supplier_purchase_5_percent_split_2_5_each(self):
        self.supplier.gstin = "32ABCDE1234F1Z5"
        self.supplier.save(update_fields=["gstin"])
        result = self.client.post(f"{self.base}/bills", {
            "supplierId": str(self.supplier.pk), "invoiceNumber": "GST-INTRA-05",
            "invoiceDate": "2026-11-06", "placeOfSupply": "Kerala",
            "items": [{"description": "Service part", "quantity": "1",
                       "purchaseRate": "1000.00", "gstRate": "5",
                       "gstTreatment": "taxable"}],
        }, format="json")
        self.assertEqual(result.status_code, 201, getattr(result, "data", None))
        self.assertEqual(result.data["taxMode"], "cgst_sgst")
        self.assertEqual(result.data["tax"], "50.00")
        self.assertEqual(result.data["cgst"], "25.00")
        self.assertEqual(result.data["sgst"], "25.00")
        self.assertEqual(result.data["igst"], "0.00")
        self.assertEqual(result.data["total"], "1050.00")
        self.assertTrue(result.data["taxComponentsVerified"])
        self.assertEqual(result.data["items"][0]["gstTreatment"], "taxable")

    def test_inter_state_supplier_purchase_18_percent_igst(self):
        self.supplier.gstin = "29ABCDE1234F1Z5"
        self.supplier.save(update_fields=["gstin"])
        result = self.client.post(f"{self.base}/bills", {
            "supplierId": str(self.supplier.pk), "invoiceNumber": "GST-INTER-18",
            "invoiceDate": "2026-11-06", "placeOfSupply": "Kerala",
            "items": [{"description": "Service part", "quantity": "1",
                       "purchaseRate": "1000.00", "gstRate": "18"}],
        }, format="json")
        self.assertEqual(result.status_code, 201, getattr(result, "data", None))
        self.assertEqual(result.data["taxMode"], "igst")
        self.assertEqual(result.data["igst"], "180.00")
        self.assertEqual(result.data["cgst"], "0.00")
        self.assertEqual(result.data["sgst"], "0.00")
        self.assertEqual(result.data["total"], "1180.00")

    def test_utgst_is_distinct_from_sgst(self):
        self.supplier.gstin = "04ABCDE1234F1Z5"
        self.supplier.save(update_fields=["gstin"])
        result = self.client.post(f"{self.base}/bills", {
            "supplierId": str(self.supplier.pk), "invoiceNumber": "GST-CHANDIGARH-05",
            "invoiceDate": "2026-11-06", "placeOfSupply": "Chandigarh",
            "items": [{"description": "Service part", "quantity": "1",
                       "purchaseRate": "1000.00", "gstRate": "5"}],
        }, format="json")
        self.assertEqual(result.status_code, 201, getattr(result, "data", None))
        self.assertEqual(result.data["cgst"], "25.00")
        self.assertEqual(result.data["utgst"], "25.00")
        self.assertEqual(result.data["sgst"], "0.00")
        self.assertEqual(result.data["total"], "1050.00")

    def test_zero_rate_requires_explicit_tax_treatment_when_registered(self):
        self.supplier.gstin = "32ABCDE1234F1Z5"
        self.supplier.save(update_fields=["gstin"])
        row = {"description": "Classified supply", "quantity": "1",
               "purchaseRate": "1000.00", "gstRate": "0"}
        data = {"supplierId": str(self.supplier.pk),
                "invoiceNumber": "GST-ZERO-1", "invoiceDate": "2026-11-06",
                "placeOfSupply": "Kerala", "items": [row]}
        invalid = self.client.post(f"{self.base}/bills", data, format="json")
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(SupplierBill.objects.filter(invoice_number="GST-ZERO-1").count(), 0)
        valid = self.client.post(f"{self.base}/bills", {
            **data, "items": [{**row, "gstTreatment": "exempt"}],
        }, format="json")
        self.assertEqual(valid.status_code, 201, getattr(valid, "data", None))
        self.assertEqual(valid.data["tax"], "0.00")

    def test_unregistered_supplier_cannot_charge_gst(self):
        data = {"supplierId": str(self.supplier.pk), "invoiceNumber": "GST-UNREG-1",
                "invoiceDate": "2026-11-06", "placeOfSupply": "Kerala",
                "items": [{"description": "Goods", "quantity": "1",
                           "purchaseRate": "1000.00", "gstRate": "18"}]}
        invalid = self.client.post(f"{self.base}/bills", data, format="json")
        self.assertEqual(invalid.status_code, 400)
        valid = self.client.post(f"{self.base}/bills", {
            **data, "items": [{**data["items"][0], "gstRate": "0"}],
        }, format="json")
        self.assertEqual(valid.status_code, 201, getattr(valid, "data", None))
        self.assertEqual(valid.data["taxMode"], "unregistered")
        self.assertEqual(valid.data["items"][0]["gstTreatment"], "unregistered")

    def test_real_example_200_part_100_paid_then_multi_bill_settlement(self):
        """₹200 purchase/₹100 payment leaves ₹100 due; later credit remains exact."""
        part_bill = self.create_bill("200.00", receive=True, sku_item=True)
        posted = self.post_bill(part_bill)
        self.assertEqual(posted.status_code, 200, getattr(posted, "data", None))
        self.assertEqual(self.summary()["outstanding"], "200.00")
        self.assertEqual(self.account_balance(), "20000.00")  # credit is not cash movement
        first = self.pay(
            [{"billId": part_bill, "amount": "100.00"}],
            amount="100.00", key="parts-one-hundred-now",
        )
        self.assertEqual(first.status_code, 201, getattr(first, "data", None))
        self.assertEqual(self.summary()["outstanding"], "100.00")
        self.assertEqual(self.account_balance(), "19900.00")

        second_bill = self.create_bill("500.00")
        self.assertEqual(self.post_bill(second_bill).status_code, 200)
        self.assertEqual(self.summary()["outstanding"], "600.00")
        settle = self.pay(
            [
                {"billId": part_bill, "amount": "100.00"},
                {"billId": second_bill, "amount": "400.00"},
            ],
            amount="500.00", key="supplier-five-hundred-multiple-bills",
        )
        self.assertEqual(settle.status_code, 201, getattr(settle, "data", None))
        self.assertEqual(self.summary()["outstanding"], "100.00")
        self.assertEqual(self.account_balance(), "19400.00")
        self.assertEqual(SupplierPayment.objects.count(), 2)
        self.assertEqual(WorkshopAccountEntry.objects.count(), 2)

        activity = self.client.get("/api/v1/finance/payment-activity?page_size=1000")
        self.assertEqual(activity.status_code, 200, getattr(activity, "data", None))
        supplier_rows = [row for row in activity.data["results"] if row["source"] == "supplier"]
        self.assertEqual(len(supplier_rows), 2)
        self.assertEqual(activity.data["supplierOutstanding"], "100.00")
        self.assertEqual(activity.data["supplierPayments"], "600.00")
        dash = self.client.get("/api/v1/finance/dashboard")
        self.assertEqual(dash.status_code, 200, getattr(dash, "data", None))
        self.assertEqual(dash.data["totalPaid"], "600.00")
        self.assertEqual(dash.data["supplierOutstanding"], "100.00")

    def test_generic_supplier_expense_and_outgoing_are_blocked_from_double_posting(self):
        generic_payment = self.client.post("/api/v1/payments", {
            "date": "2026-11-06", "direction": "OUT", "amount": "100.00",
            "method": "Bank Transfer", "account": self.account_id,
            "status": "Completed", "category": "Supplier Payment",
        }, format="json")
        self.assertEqual(generic_payment.status_code, 400)
        duplicate_expense = self.client.post("/api/v1/expenses", {
            "date": "2026-11-06", "amount": "100.00",
            "category": "Spare Parts", "status": "Paid",
            "account": self.account_id, "payment_method": "Bank Transfer",
        }, format="json")
        self.assertEqual(duplicate_expense.status_code, 400)
        self.assertEqual(WorkshopAccountEntry.objects.count(), 0)

    def test_supplier_purchase_split_cash_bank_credit_and_stock_once(self):
        cash = self.client.post(f"{self.base}/accounts", {
            "name": "Counter Cash", "kind": "Cash", "openingBalance": "150.00",
        }, format="json")
        self.assertEqual(cash.status_code, 201, getattr(cash, "data", None))
        body = {
            "supplierId": str(self.supplier.pk),
            "invoiceNumber": "SPLIT-300-PURCHASE",
            "invoiceDate": "2026-11-06",
            "receiveIntoStock": True, "postNow": True,
            "items": [{
                "itemId": str(self.stock.pk),
                "description": "Paint",
                "quantity": "1", "purchaseRate": "300.00", "gstRate": "0",
                "sellingPrice": "450.00", "updateItemPrices": True,
            }],
            "payments": [
                {"accountId": cash.data["id"], "amount": "100.00",
                 "method": "Cash", "kind": "Payment", "paidOn": "2026-11-06",
                 "idempotencyKey": "split-cash-100-test"},
                {"accountId": self.account_id, "amount": "100.00",
                 "method": "Bank Transfer", "kind": "Payment", "paidOn": "2026-11-06",
                 "idempotencyKey": "split-bank-100-test"},
            ],
        }
        resp = self.client.post(f"{self.base}/bills", body, format="json")
        self.assertEqual(resp.status_code, 201, getattr(resp, "data", None))
        self.assertEqual(resp.data["paid"], "200.00")
        self.assertEqual(resp.data["outstanding"], "100.00")
        self.assertEqual(len(resp.data["payments"]), 2)
        self.assertTrue(resp.data["createdAt"])
        self.assertTrue(resp.data["postedAt"])
        self.stock.refresh_from_db()
        self.assertEqual(str(self.stock.on_hand), "3.00")
        self.assertEqual(str(self.stock.cost_price), "300.00")
        self.assertEqual(str(self.stock.selling_price), "450.00")
        self.assertEqual(float(self.stock.tax), 0.0)
        self.assertEqual(SupplierBill.objects.count(), 1)
        self.assertEqual(SupplierPayment.objects.count(), 2)
        self.assertEqual(WorkshopAccountEntry.objects.count(), 2)
        self.assertEqual(self.summary()["outstanding"], "100.00")
        accounts = self.client.get(f"{self.base}/accounts")
        self.assertEqual(accounts.status_code, 200)
        current = {row["id"]: row["recordedBalance"] for row in accounts.data}
        self.assertEqual(current[cash.data["id"]], "50.00")
        self.assertEqual(current[self.account_id], "19900.00")
        retry = self.client.post(f"{self.base}/bills", body, format="json")
        self.assertEqual(retry.status_code, 400)
        self.assertEqual(SupplierPayment.objects.count(), 2)

    def test_split_exceeding_total_rolls_back_bill_and_stock_and_ledger(self):
        start_stock = str(self.stock.on_hand)
        start_count = WorkshopAccountEntry.objects.count()
        resp = self.client.post(f"{self.base}/bills", {
            "supplierId": str(self.supplier.pk),
            "invoiceNumber": "SPLIT-ROLLBACK-1",
            "invoiceDate": "2026-11-06",
            "receiveIntoStock": True, "postNow": True,
            "items": [{
                "itemId": str(self.stock.pk), "description": "Paint",
                "quantity": "1", "purchaseRate": "200.00", "gstRate": "0",
            }],
            "payments": [{
                "accountId": self.account_id, "amount": "250.00",
                "method": "Bank Transfer", "paidOn": "2026-11-06",
                "idempotencyKey": "invalid-over-total",
            }],
        }, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(SupplierBill.objects.filter(invoice_number="SPLIT-ROLLBACK-1").exists())
        self.stock.refresh_from_db()
        self.assertEqual(str(self.stock.on_hand), start_stock)
        self.assertEqual(WorkshopAccountEntry.objects.count(), start_count)
