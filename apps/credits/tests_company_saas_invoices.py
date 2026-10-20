"""Company SaaS billing statement isolation, PDF rendering, and read-only behavior."""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.companies.models import Company
from apps.credits.company_saas_invoices import (
    company_saas_invoices, get_company_saas_invoice,
)
from apps.credits.models import (
    BillingCycleStatement, CreditCycleInvoice, CreditLedger, CreditWallet,
    MonthlyUsageInvoice,
)
from apps.credits.saas_invoice_pdf import create_saas_credit_statement_pdf
from apps.roles.models import Role


class CompanySaaSInvoiceTests(TestCase):
    def setUp(self):
        self.first = Company.objects.create(name="Alpha Workshop", slug="alpha-workshop-invoice")
        self.second = Company.objects.create(name="Beta Workshop", slug="beta-workshop-invoice")
        now = timezone.now()
        self.calendar = BillingCycleStatement.objects.create(
            company=self.first, month="2026-09", due_at=now - timedelta(days=1),
            feature_credits=Decimal("12.50"), storage_credits=Decimal("2.25"),
            total_credits=Decimal("14.75"), status="pending",
            finalized_at=now - timedelta(days=5),
            details={"items": [{"kind": "feature", "credits": "12.50"}]},
        )
        self.monthly = MonthlyUsageInvoice.objects.create(
            company=self.first, category="feature",
            billing_month="2026-08", credits_due=Decimal("4.50"), status="paid",
            settled_at=now - timedelta(days=6),
        )
        self.cycle = CreditCycleInvoice.objects.create(
            company=self.first, category="storage",
            starts_on=(now - timedelta(days=37)).date(),
            ends_at=now - timedelta(days=7), due_at=now - timedelta(days=3),
            credits_due=Decimal("3.00"), status="insufficient",
        )
        BillingCycleStatement.objects.create(
            company=self.second, month="2026-09",
            due_at=now + timedelta(days=5),
            total_credits=Decimal("999"), status="pending",
        )
        self.role = Role.objects.create(
            company=None, name="SaaS Admin", code="platform_admin_test",
            permissions=["panel.companies.view"], is_active=True,
        )
        self.admin = User.objects.create_user(
            email="platform@example.test", password="example-secret",
            name="Platform Admin", role=self.role, is_staff=True,
        )

    def test_company_list_only_reads_issued_credit_records(self):
        documents = company_saas_invoices(self.first)
        self.assertEqual(len(documents), 3)
        self.assertEqual({doc["source"] for doc in documents},
                         {"calendar", "monthly", "cycle"})
        self.assertNotIn(Decimal("999"), [doc["total_credits"] for doc in documents])
        calendar = next(doc for doc in documents if doc["source"] == "calendar")
        self.assertEqual(calendar["total_credits"], Decimal("14.75"))
        self.assertEqual(calendar["feature_credits"], Decimal("12.50"))
        self.assertEqual(calendar["storage_credits"], Decimal("2.25"))
        self.assertTrue(calendar["is_overdue"])
        monthly = next(doc for doc in documents if doc["source"] == "monthly")
        self.assertIsNone(monthly["due_at"])
        self.assertEqual(monthly["status"], "paid")

    def test_invalid_source_and_cross_company_pdf_are_not_found(self):
        from django.http import Http404
        with self.assertRaises(Http404):
            get_company_saas_invoice(self.second, "calendar", self.calendar.pk)
        with self.assertRaises(Http404):
            get_company_saas_invoice(self.first, "workshop", self.calendar.pk)

    def test_pdf_is_credit_statement_and_does_not_mutate_wallet(self):
        CreditWallet.objects.create(company=self.first, balance=Decimal("50"))
        initial_ledgers = CreditLedger.objects.count()
        invoice = get_company_saas_invoice(self.first, "calendar", self.calendar.pk)
        pdf = create_saas_credit_statement_pdf(self.first, invoice)
        self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertGreater(len(pdf), 1000)
        self.assertEqual(CreditLedger.objects.count(), initial_ledgers)
        self.assertEqual(CreditWallet.objects.get(company=self.first).balance, Decimal("50"))

    def test_authenticated_company_admin_can_download_but_not_other_company_document(self):
        self.client.force_login(self.admin)
        url = reverse("panel:company-saas-invoice-pdf", kwargs={
            "pk": self.first.pk, "source": "calendar", "invoice_id": self.calendar.pk,
        })
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertTrue(response.content.startswith(b"%PDF-"))
        other = reverse("panel:company-saas-invoice-pdf", kwargs={
            "pk": self.second.pk, "source": "calendar", "invoice_id": self.calendar.pk,
        })
        self.assertEqual(self.client.get(other).status_code, 404)
        self.assertEqual(self.client.post(url).status_code, 405)

    def test_anonymous_user_is_redirected_to_login(self):
        url = reverse("panel:company-saas-invoice-pdf", kwargs={
            "pk": self.first.pk, "source": "calendar", "invoice_id": self.calendar.pk,
        })
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_page_template_compiles(self):
        from django.template.loader import get_template
        self.assertIsNotNone(get_template("control_panel/company_detail.html"))
