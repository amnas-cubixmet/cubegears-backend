"""Part 10: invoice immutability, audited cancellation and tenant-safe PDFs."""

from datetime import date
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.invoices.models import Invoice
from apps.roles.models import Role
from apps.saas.models import CompanySetting


@override_settings(CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
                   CREDIT_LABEL_ONLY_ACCESS_ENABLED=False)
class InvoiceDocumentIntegrityTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Invoicing Garage", slug="part-ten-garage", require_paid_features=False,
        )
        self.other = Company.objects.create(
            name="Other Garage", slug="part-ten-other", require_paid_features=False,
        )
        self.role = Role.objects.create(
            company=self.company, name="Owner", code="OWNER_PART10",
            permissions=["*"], is_system=True,
        )
        self.owner = User.objects.create_user(
            email="part-ten-owner@example.test", name="Owner",
            password="StrongPass123!", company=self.company, role=self.role,
        )
        self.company.owner = self.owner
        self.company.save(update_fields=["owner"])
        self.customer = Customer.objects.create(
            company=self.company, name="Customer A", phone="+919888877766",
        )
        self.foreign_customer = Customer.objects.create(
            company=self.other, name="Customer B", phone="+919877766655",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)

    def document(self, *, number="EST-PART10-01", kind="estimate", status="Draft", company=None, customer=None):
        return Invoice.objects.create(
            company=company or self.company, customer=customer or self.customer,
            number=number, kind=kind, status=status, date=date(2026, 11, 7),
        )

    def url(self, document):
        return f"/api/v1/billing/documents/{document.pk}"

    def test_draft_cancellation_requires_reason_and_retains_record(self):
        doc = self.document()
        invalid = self.client.post(self.url(doc) + "/cancel", {"reason": "no"}, format="json")
        self.assertEqual(invalid.status_code, 400, invalid.data)
        self.assertEqual(Invoice.objects.get(pk=doc.pk).status, "Draft")
        accepted = self.client.post(
            self.url(doc) + "/cancel", {"reason": "Customer withdrew request"}, format="json",
        )
        self.assertEqual(accepted.status_code, 200, accepted.data)
        doc.refresh_from_db()
        self.assertEqual(doc.cancellation_reason, "Customer withdrew request")
        self.assertEqual(doc.status, "Cancelled")
        self.assertIsNotNone(doc.cancelled_at)
        self.assertEqual(accepted.data["cancellationReason"], doc.cancellation_reason)
        retry = self.client.post(self.url(doc) + "/cancel", {}, format="json")
        self.assertEqual(retry.status_code, 200, retry.data)
        self.assertEqual(doc.cancellation_reason, "Customer withdrew request")
        self.assertEqual(self.client.delete(self.url(doc)).status_code, 400)
        self.assertTrue(Invoice.objects.filter(pk=doc.pk).exists())

    def test_issued_estimate_is_locked_and_not_cancellable(self):
        doc = self.document()
        finish = self.client.post(self.url(doc) + "/finalize", {}, format="json")
        self.assertEqual(finish.status_code, 200, finish.data)
        doc.refresh_from_db()
        self.assertEqual(doc.status, "Issued")
        self.assertTrue(doc.finalized_at)
        saved = (doc.number, doc.notes, doc.template_snapshot.copy())
        changed = self.client.patch(self.url(doc), {"notes": "Edited after issue"}, format="json")
        self.assertEqual(changed.status_code, 400, changed.data)
        self.assertEqual(self.client.delete(self.url(doc)).status_code, 400)
        self.assertEqual(self.client.post(
            self.url(doc) + "/cancel", {"reason": "Try to void issued estimate"}, format="json",
        ).status_code, 400)
        doc.refresh_from_db()
        self.assertEqual((doc.number, doc.notes, doc.template_snapshot), saved)

    def test_template_snapshot_stays_frozen_when_settings_change(self):
        CompanySetting.objects.create(
            company=self.company, category="document_templates",
            data={"estimateTemplate": "classic", "accent": "blue"},
        )
        doc = self.document(number="EST-PART10-02")
        response = self.client.post(self.url(doc) + "/finalize", {}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        doc.refresh_from_db()
        self.assertEqual(doc.template_snapshot["selectedTemplate"], "classic")
        CompanySetting.objects.filter(
            company=self.company, category="document_templates",
        ).update(data={"estimateTemplate": "minimal", "accent": "teal"})
        self.assertEqual(self.client.get(self.url(doc)).data["templateSnapshot"]["selectedTemplate"], "classic")

    def test_pdf_private_and_other_workshop_is_inaccessible(self):
        doc = self.document(number="EST-PART10-03")
        response = self.client.get(self.url(doc) + "/pdf", HTTP_ACCEPT="application/pdf")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("no-store", response["Cache-Control"])
        foreign = self.document(number="EST-OTHER", company=self.other, customer=self.foreign_customer)
        self.assertEqual(self.client.get(self.url(foreign) + "/pdf").status_code, 404)
        self.assertEqual(self.client.post(
            self.url(foreign) + "/cancel", {"reason": "Should not work"}, format="json",
        ).status_code, 404)
