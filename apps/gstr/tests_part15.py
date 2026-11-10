"""Part 15 full-stack GST 2B, ITC review, credit-note and tax-ledger checks."""
import json
from datetime import date
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.invoices.models import Invoice
from apps.inventory.models import Supplier, SupplierBill, SupplierDebitNote
from apps.roles.models import Role
from .models import (
    GSTR2BImport, GSTFilingReview, GSTInputTaxClaim,
    GSTInputTaxReversal, GSTSalesCreditNote,
)


@override_settings(
    CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
    CREDIT_LABEL_ONLY_ACCESS_ENABLED=False,
)
class GSTPart15IntegrationTests(TestCase):
    root = "/api/v1/gstr"
    period = "2026-10"

    def setUp(self):
        self.company = Company.objects.create(
            name="GST Workshop", slug="part15-gst-workshop",
            gstin="32ABCDE1234F1Z5",
        )
        self.branch = Branch.objects.create(
            company=self.company, name="Main", code="GST15", is_active=True,
        )
        role = Role.objects.create(
            company=self.company, name="Owner", code="PART15_OWNER",
            permissions=["*"], is_active=True, is_system=True,
        )
        self.user = User.objects.create_user(
            email="part15@example.test", name="Owner",
            password="StrongPassword123", company=self.company,
            branch=self.branch, role=role,
        )
        self.company.owner = self.user
        self.company.save(update_fields=["owner"])
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.supplier = Supplier.objects.create(
            company=self.company, branch=self.branch,
            name="Auto Supplier", gstin="32ABCDE6789G1Z5",
        )
        self.bill = SupplierBill.objects.create(
            company=self.company, branch=self.branch, supplier=self.supplier,
            invoice_number="PART15-001", invoice_date=date(2026, 10, 14),
            status="Posted", receive_into_stock=False, posted_at=timezone.now(),
            items=[{
                "lineId": "tested-gst-line", "description": "Workshop parts",
                "quantity": "2.00", "purchaseRate": "100.00",
                "subtotal": "200.00", "cgst": "18.00", "sgst": "18.00",
                "utgst": "0.00", "igst": "0.00", "tax": "36.00",
                "total": "236.00",
                "taxMode": "cgst_sgst", "taxValidationStatus": "valid",
            }],
            subtotal=Decimal("200.00"), tax=Decimal("36.00"),
            total=Decimal("236.00"),
        )
        self.customer = Customer.objects.create(
            company=self.company, branch=self.branch,
            name="Service Customer", phone="9876001234",
        )
        self.invoice = Invoice.objects.create(
            company=self.company, branch=self.branch, customer=self.customer,
            number="INV-GST-15", kind="invoice", invoice_type="gst",
            status="Finalized", date=date(2026, 10, 16),
            finalized_at=timezone.now(), taxable=Decimal("100.00"),
            cgst=Decimal("9.00"), sgst=Decimal("9.00"),
            igst=Decimal("0.00"), total=Decimal("118.00"),
            balance=Decimal("118.00"), tax_mode="cgst_sgst",
            seller_snapshot={"gstin": self.company.gstin, "name": self.company.name},
            customer_snapshot={"name": self.customer.name, "stateCode": "32"},
        )

    def uploaded_2b(self, *, portal_gstin=None, itc_availability="Y"):
        payload = {
            "gstin": portal_gstin or self.company.gstin, "rtnprd": "102026",
            "docdata": {"b2b": [{
                "ctin": self.supplier.gstin,
                "inv": [{
                    "inum": self.bill.invoice_number,
                    "idt": "14-10-2026", "itcavl": itc_availability,
                    "itms": [{
                        "itm_det": {
                            "txval": 200, "camt": 18, "samt": 18,
                            "iamt": 0,
                        },
                    }],
                }],
            }]},
        }
        return SimpleUploadedFile(
            "official-gstr2b.json", json.dumps(payload).encode(), content_type="application/json",
        )

    def store_import(self):
        result = self.client.post(
            f"{self.root}/reconcile?period={self.period}&save=1",
            {"file": self.uploaded_2b()}, format="multipart",
        )
        self.assertEqual(result.status_code, 200, getattr(result, "data", None))
        self.assertTrue(result.data["saved"])
        self.assertEqual(result.data["counts"]["Matched"], 1)
        return result.data["importId"]

    def approve(self, import_id):
        return self.client.post(f"{self.root}/itc", {
            "period": self.period, "importId": import_id,
            "billId": str(self.bill.pk),
            "eligibilityReason": (
                "Verified business-use invoice, tax heads and statutory "
                "eligibility restrictions with the responsible tax reviewer."
            ),
        }, format="json")

    def test_gstr_2b_import_is_saved_once_and_tenant_gstin_is_strict(self):
        first = self.store_import()
        second = self.store_import()
        self.assertEqual(first, second)
        self.assertEqual(GSTR2BImport.objects.count(), 1)
        listings = self.client.get(f"{self.root}/imports?period={self.period}")
        self.assertEqual(listings.status_code, 200)
        self.assertEqual(listings.data[0]["matchedBills"][0]["billId"], str(self.bill.pk))
        mismatch = self.client.post(
            f"{self.root}/reconcile?period={self.period}&save=1",
            {"file": self.uploaded_2b(portal_gstin="33ABCDE1234F1Z5")},
            format="multipart",
        )
        self.assertEqual(mismatch.status_code, 400)
        self.assertEqual(GSTR2BImport.objects.count(), 1)

    def test_approval_is_not_automatic_or_duplicate_and_is_visible_in_3b(self):
        before = self.client.get(f"{self.root}/gstr-3b?period={self.period}")
        self.assertEqual(before.status_code, 200, getattr(before, "data", None))
        self.assertEqual(before.data["eligibleITC"], "0.00")
        import_id = self.store_import()
        result = self.approve(import_id)
        self.assertEqual(result.status_code, 201, getattr(result, "data", None))
        self.assertEqual(result.data["cgst"], "18.00")
        self.assertEqual(result.data["sgst"], "18.00")
        self.assertEqual(self.approve(import_id).status_code, 400)
        self.assertEqual(GSTInputTaxClaim.objects.count(), 1)
        after = self.client.get(f"{self.root}/gstr-3b?period={self.period}")
        self.assertEqual(after.data["eligibleITC"], "36.00")
        self.assertEqual(after.data["approvedITCByHead"]["cgst"], "18.00")
        self.assertIsNone(after.data["estimatedNetLiability"])
        ledger = self.client.get(f"{self.root}/tax-ledger?period={self.period}")
        self.assertEqual(ledger.status_code, 200)
        self.assertTrue(any(
            row["type"] == "Internally Approved ITC" for row in ledger.data["entries"]
        ))

    def test_return_after_approval_reverses_claimed_credit_exactly_once(self):
        self.approve(self.store_import())
        note = SupplierDebitNote.objects.create(
            company=self.company, branch=self.branch,
            bill=self.bill, supplier=self.supplier,
            number="RET-GST15", date=date(2026, 10, 20),
            reason="Defective purchased part returned",
            lines=[{"lineIndex": 0, "quantity": "1.00"}],
            subtotal=Decimal("100.00"), tax=Decimal("18.00"),
            total=Decimal("118.00"),
            payable_reduction=Decimal("118.00"),
            credit_amount=Decimal("0.00"),
            request_key="part15-return-once",
            created_by=self.user,
        )
        reversal = GSTInputTaxReversal.objects.get(debit_note=note)
        self.assertEqual(reversal.cgst, Decimal("9.00"))
        self.assertEqual(reversal.sgst, Decimal("9.00"))
        self.assertEqual(GSTInputTaxReversal.objects.count(), 1)
        reports = self.client.get(f"{self.root}/gstr-3b?period={self.period}")
        self.assertEqual(reports.data["reversedITC"], "18.00")
        self.assertEqual(reports.data["eligibleITC"], "36.00")
        self.assertEqual(GSTInputTaxClaim.objects.count(), 1)

    def test_claim_after_return_cannot_reclaim_returned_tax(self):
        SupplierDebitNote.objects.create(
            company=self.company, branch=self.branch,
            bill=self.bill, supplier=self.supplier,
            number="RET-BEFORE15", date=date(2026, 10, 18),
            reason="Partial return before claim",
            lines=[{"lineIndex": 0, "quantity": "1.00"}],
            subtotal=Decimal("100.00"), tax=Decimal("18.00"),
            total=Decimal("118.00"), payable_reduction=Decimal("118.00"),
            credit_amount=Decimal("0.00"),
            request_key="part15-return-early",
        )
        result = self.approve(self.store_import())
        self.assertEqual(result.status_code, 201, getattr(result, "data", None))
        self.assertEqual(result.data["total"], "18.00")
        self.assertEqual(GSTInputTaxReversal.objects.count(), 0)

    def test_sales_credit_note_is_partial_idempotent_and_reflected_only_in_note_period(self):
        path = f"{self.root}/credit-notes"
        payload = {
            "invoiceId": str(self.invoice.pk),
            "number": "GST-CREDIT-15",
            "date": "2026-10-20",
            "reason": "Customer pricing correction after service",
            "taxable": "50.00",
            "requestKey": "part15-sales-credit-001",
        }
        first = self.client.post(path, payload, format="json")
        self.assertEqual(first.status_code, 201, getattr(first, "data", None))
        self.assertEqual(first.data["cgst"], "4.50")
        self.assertEqual(first.data["sgst"], "4.50")
        self.assertEqual(self.client.post(path, payload, format="json").status_code, 200)
        self.assertEqual(GSTSalesCreditNote.objects.count(), 1)
        tamper = self.client.post(path, {
            **payload, "taxable": "60.00",
        }, format="json")
        self.assertEqual(tamper.status_code, 400)
        extra = self.client.post(path, {
            **payload, "number": "GST-CREDIT-EXCESS", "requestKey": "part15-excess",
            "taxable": "51.00",
        }, format="json")
        self.assertEqual(extra.status_code, 400)
        gstr1 = self.client.get(f"{self.root}/gstr-1?period={self.period}")
        self.assertEqual(gstr1.data["netTotals"]["taxable"], "50.00")
        self.assertEqual(gstr1.data["creditTotals"]["cgst"], "4.50")
        overview = self.client.get(f"{self.root}/overview?period={self.period}")
        self.assertEqual(overview.data["overview"]["outputGST"], "9.00")
        self.assertEqual(overview.data["overview"]["salesCreditNotesCount"], 1)

    def test_filing_evidence_requires_reference_never_claims_portal_verification(self):
        uri = f"{self.root}/filing-review?period={self.period}"
        missing = self.client.post(uri, {
            "status": "Reported Filed",
            "note": "Reviewed return with source accounting data.",
        }, format="json")
        self.assertEqual(missing.status_code, 400)
        payload = {
            "status": "Reported Filed", "reference": "GST-ACK-TEST-001",
            "filedOn": "2026-11-07",
            "note": "Reviewed return and recorded GST Portal acknowledgment.",
        }
        result = self.client.post(uri, payload, format="json")
        self.assertEqual(result.status_code, 201, getattr(result, "data", None))
        self.assertFalse(result.data["portalVerified"])
        self.assertEqual(GSTFilingReview.objects.count(), 1)
        self.assertEqual(self.client.post(uri, payload, format="json").status_code, 200)
        self.assertEqual(self.client.post(uri, {
            **payload, "reference": "OTHER-ACK",
        }, format="json").status_code, 400)
        filing = self.client.get(uri)
        self.assertFalse(filing.data["portalVerified"])

    def test_saved_2b_reconciliation_is_readable_after_refresh(self):
        import_id = self.store_import()
        endpoint = f"{self.root}/reconcile?period={self.period}"
        result = self.client.get(f"{endpoint}&importId={import_id}")
        self.assertEqual(result.status_code, 200, getattr(result, "data", None))
        self.assertEqual(result.data["importId"], import_id)
        self.assertTrue(result.data["saved"])
        self.assertEqual(result.data["counts"]["Matched"], 1)
        self.assertEqual(result.data["rows"][0]["bookId"], str(self.bill.pk))
        self.assertEqual(self.client.get(endpoint).status_code, 400)
        self.assertEqual(self.client.get(f"{endpoint}&importId=invalid").status_code, 400)
        from uuid import uuid4
        self.assertEqual(self.client.get(
            f"{endpoint}&importId={uuid4()}",
        ).status_code, 404)
        self.assertEqual(self.client.get(
            f"{self.root}/reconcile?period=2026-09&importId={import_id}",
        ).status_code, 404)

    def test_tax_ledger_excludes_unverified_seller_snapshot(self):
        first = self.client.get(f"{self.root}/tax-ledger?period={self.period}")
        self.assertEqual(first.status_code, 200)
        self.assertTrue(any(row["sourceId"] == str(self.invoice.pk)
                            for row in first.data["entries"]))
        self.invoice.seller_snapshot = {
            "gstin": "33ABCDE1234F1Z5", "name": self.company.name,
        }
        self.invoice.save(update_fields=["seller_snapshot"])
        prepared = self.client.get(f"{self.root}/overview?period={self.period}")
        self.assertEqual(prepared.status_code, 200)
        self.assertEqual(prepared.data["gstr1"]["rows"], [])
        ledger = self.client.get(f"{self.root}/tax-ledger?period={self.period}")
        self.assertEqual(ledger.status_code, 200)
        self.assertFalse(any(row["sourceId"] == str(self.invoice.pk)
                             for row in ledger.data["entries"]))

    def test_other_workshop_cannot_reuse_purchase_or_import(self):
        import_id = self.store_import()
        other_company = Company.objects.create(
            name="Outside", slug="part15-outsider",
            gstin="33ABCDE1234F1Z5",
        )
        other_branch = Branch.objects.create(
            company=other_company, name="Other", code="OTHER15", is_active=True,
        )
        role = Role.objects.create(
            company=other_company, name="Owner", code="OTHER15",
            permissions=["*"], is_active=True,
        )
        other_user = User.objects.create_user(
            email="other15@example.test", name="Other", password="StrongPassword123",
            company=other_company, branch=other_branch, role=role,
        )
        other_company.owner = other_user
        other_company.save(update_fields=["owner"])
        client = APIClient()
        client.force_authenticate(other_user)
        other = client.post(f"{self.root}/itc", {
            "period": self.period, "importId": import_id,
            "billId": str(self.bill.pk),
            "eligibilityReason": (
                "A fraudulent cross-tenant approval request must be rejected."
            ),
        }, format="json")
        self.assertEqual(other.status_code, 404)
        self.assertEqual(GSTInputTaxClaim.objects.count(), 0)
