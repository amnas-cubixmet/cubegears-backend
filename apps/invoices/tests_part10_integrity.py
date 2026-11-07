"""Part 10: financial-integrity guarantees on real invoice REST endpoints.

No draft/form API is permitted to fabricate a payment, finalize itself,
rewrite an invoice number or remove a permanently allocated audit record.
"""
from datetime import date
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.invoices.models import Invoice, InvoiceItem
from apps.roles.models import Role
from apps.vehicles.models import Vehicle


@override_settings(
    CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
    CREDIT_LABEL_ONLY_ACCESS_ENABLED=False,
)
class InvoiceIntegrityWorkflowTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Part 10 Garage", slug="part10-garage", require_paid_features=False,
        )
        self.branch = Branch.objects.create(
            company=self.company, name="Main Workshop", code="MAIN",
        )
        role = Role.objects.create(
            company=self.company, name="Owner", code="PART10_OWNER",
            permissions=["*"], is_active=True,
        )
        self.owner = User.objects.create_user(
            email="part10-owner@example.test", password="StrongPass123!",
            name="Owner", company=self.company, role=role, branch=self.branch,
        )
        self.company.owner = self.owner
        self.company.save(update_fields=["owner"])
        self.customer = Customer.objects.create(
            company=self.company, branch=self.branch,
            name="Buyer", phone="+919885551234",
        )
        self.other_customer = Customer.objects.create(
            company=self.company, branch=self.branch,
            name="Other Buyer", phone="+919886661234",
        )
        self.vehicle = Vehicle.objects.create(
            company=self.company, branch=self.branch,
            customer=self.other_customer, registration="KL58ZZ7890",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)

    def draft(self, **changes):
        payload = {
            "kind": "invoice", "date": date.today().isoformat(),
            "customer": str(self.customer.pk),
            "items": [{
                "type": "Labour", "description": "Oil and filter service",
                "qty": "1.00", "rate": "1200.00",
                "discount": "0", "taxRate": "0", "unit": "JOB",
            }],
        }
        payload.update(changes)
        return self.client.post("/api/v1/billing/documents", payload, format="json")

    def test_save_draft_cannot_fabricate_a_payment_or_finalization(self):
        result = self.draft(
            status="Paid", paid="1200.00", balance="0",
            total="0", finalized_at="2026-01-01T10:00:00Z",
        )
        self.assertEqual(result.status_code, 201, result.data)
        saved = Invoice.objects.get(pk=result.data["id"])
        self.assertEqual(saved.status, "Draft")
        self.assertEqual(saved.paid, 0)
        self.assertEqual(saved.balance, saved.total)
        self.assertIsNone(saved.finalized_at)

    def test_draft_edit_can_change_notes_but_not_paid_status_or_number(self):
        result = self.draft()
        self.assertEqual(result.status_code, 201, result.data)
        number = result.data["number"]
        url = f"/api/v1/billing/documents/{result.data['id']}"
        altered = self.client.patch(url, {
            "notes": "Tyre inspection complete",
            "status": "Paid", "paid": "500.00", "total": "1",
        }, format="json")
        self.assertEqual(altered.status_code, 200, altered.data)
        saved = Invoice.objects.get(pk=result.data["id"])
        self.assertEqual(saved.notes, "Tyre inspection complete")
        self.assertEqual(saved.status, "Draft")
        self.assertEqual(saved.paid, 0)
        self.assertEqual(saved.number, number)
        rename = self.client.patch(url, {"number": "INVOICE-OTHER"}, format="json")
        self.assertEqual(rename.status_code, 400, rename.data)
        saved.refresh_from_db()
        self.assertEqual(saved.number, number)

    def test_number_allocation_skips_gaps_and_never_reuses_cancelled_number(self):
        first = self.draft()
        second = self.draft()
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(second.status_code, 201, second.data)
        self.assertNotEqual(first.data["number"], second.data["number"])
        cancelled = self.client.post(
            f"/api/v1/billing/documents/{second.data['id']}/cancel",
            {"reason": "Wrong document draft, keep audit history"}, format="json",
        )
        self.assertEqual(cancelled.status_code, 200, cancelled.data)
        third = self.draft()
        self.assertEqual(third.status_code, 201, third.data)
        self.assertTrue(third.data["number"].endswith("-0003"))
        self.assertEqual(self.client.delete(
            f"/api/v1/billing/documents/{second.data['id']}"
        ).status_code, 400)
        self.assertTrue(Invoice.objects.filter(pk=second.data["id"]).exists())

    def test_invoice_cannot_reference_another_customers_vehicle(self):
        invalid = self.draft(vehicle=str(self.vehicle.pk))
        self.assertEqual(invalid.status_code, 400, invalid.data)
        self.assertEqual(Invoice.objects.filter(company=self.company).count(), 0)

    def test_estimate_conversion_is_safe_to_retry(self):
        quote = Invoice.objects.create(
            company=self.company, branch=self.branch,
            customer=self.customer, number="EST-PART10-001",
            kind="estimate", status="Issued", date=date.today(),
        )
        InvoiceItem.objects.create(
            invoice=quote, item_type="Labour", description="Repair estimate",
            quantity=1, rate=1200, tax_rate=0,
        )
        path = f"/api/v1/billing/documents/{quote.pk}/convert-to-invoice"
        initial = self.client.post(path, {}, format="json")
        self.assertEqual(initial.status_code, 201, initial.data)
        repeated = self.client.post(path, {}, format="json")
        self.assertEqual(repeated.status_code, 200, repeated.data)
        self.assertEqual(initial.data["id"], repeated.data["id"])
        self.assertEqual(Invoice.objects.filter(
            company=self.company, kind="invoice", source_estimate=quote,
        ).count(), 1)

    def test_cross_workshop_document_is_not_exposed(self):
        another_company = Company.objects.create(
            name="Foreign Garage", slug="foreign-part10",
        )
        another_customer = Customer.objects.create(
            company=another_company, name="Foreign Buyer", phone="+919009999999",
        )
        foreign = Invoice.objects.create(
            company=another_company, customer=another_customer,
            number="FOREIGN-INV-001", kind="invoice", date=date.today(),
        )
        self.assertEqual(self.client.get(
            f"/api/v1/billing/documents/{foreign.pk}"
        ).status_code, 404)
        self.assertEqual(self.client.delete(
            f"/api/v1/billing/documents/{foreign.pk}"
        ).status_code, 404)
