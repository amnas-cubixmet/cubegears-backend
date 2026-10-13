import re
from datetime import date
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.roles.models import Role
from .models import Invoice, InvoiceItem


class InvoicePdfTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Test Workshop", legal_name="Test Workshop Pvt Ltd",
            slug="pdf-workshop", gstin="32ABCDE1234F1Z5",
            address="Sample Road, Kochi", phone="9876543210",
        )
        self.customer = Customer.objects.create(
            company=self.company, name="Invoice Client", phone="9000011111",
            address="Market Road", gstin="32ABCDE6789P1Z6",
        )
        self.role = Role.objects.create(
            company=self.company, name="Invoice Viewer",
            code="INVOICE_VIEW", permissions=["invoices.view"],
        )
        self.user = User.objects.create(
            company=self.company, role=self.role, name="Viewer",
            email="viewer@pdf-workshop.test",
        )
        self.invoice = Invoice.objects.create(
            company=self.company, number="INV-2026/42",
            kind="invoice", invoice_type="gst", status="Finalized",
            customer=self.customer, date=date(2026, 10, 13),
            seller_snapshot={"name": "Test Workshop Pvt Ltd", "gstin": "32ABCDE1234F1Z5"},
            customer_snapshot={"name": "Invoice Client", "placeOfSupply": "32-Kerala"},
            taxable=Decimal("1000.00"), igst=Decimal("180.00"),
            total=Decimal("1180.00"), paid=Decimal("180.00"), balance=Decimal("1000.00"),
            payment_mode="UPI", notes="Final job invoice",
            terms_conditions="Thank you for choosing our workshop.",
        )
        InvoiceItem.objects.create(
            invoice=self.invoice, description="Full engine service",
            item_type="Labour", quantity=Decimal("1.00"), rate=Decimal("1000.00"),
            tax_rate=Decimal("18.00"), hsn_sac="998729",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.url = f"/api/v1/billing/documents/{self.invoice.id}/pdf"

    def test_downloads_a4_invoice_pdf_as_attachment(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertIn('INV-2026-42.pdf', response["Content-Disposition"])
        self.assertTrue(response.content.startswith(b"%PDF-"))
        self.assertGreater(len(response.content), 2500)
        self.assertIn(b"/Type /Page", response.content)

    def test_draft_is_watermarked_not_automatically_finalized(self):
        self.invoice.status = "Draft"
        self.invoice.save(update_fields=["status"])
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, "Draft")
        self.assertTrue(response.content.startswith(b"%PDF-"))

    def test_long_invoice_spans_pages_without_truncating_rows(self):
        InvoiceItem.objects.bulk_create([
            InvoiceItem(
                invoice=self.invoice, description=f"Long part {i}: " + "Air-filter service " * 7,
                item_type="Stock Part", quantity=Decimal("2.00"),
                rate=Decimal("120.00"), tax_rate=Decimal("18.00")
            ) for i in range(70)
        ])
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        page_count = len(re.findall(rb"/Type\s*/Page\b", response.content))
        self.assertGreaterEqual(page_count, 2)

    def test_other_company_invoice_not_visible(self):
        other = Company.objects.create(name="Other Garage", slug="other-pdf-garage")
        client = Customer.objects.create(company=other, name="Other", phone="9555511111")
        private_invoice = Invoice.objects.create(
            company=other, number="PRIVATE-1", date=date(2026, 10, 13), customer=client,
        )
        response = self.client.get(f"/api/v1/billing/documents/{private_invoice.id}/pdf")
        self.assertEqual(response.status_code, 404)

    def test_requires_invoice_view_permission(self):
        self.role.permissions = ["jobs.view"]
        self.role.save(update_fields=["permissions"])
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_anonymous_cannot_fetch_invoice_pdf(self):
        self.client.force_authenticate(user=None)
        response = self.client.get(self.url)
        self.assertIn(response.status_code, (401, 403))
