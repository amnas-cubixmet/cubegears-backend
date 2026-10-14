"""Invoice/estimate Control Panel UI and billing data safety regression checks."""
from datetime import date
from decimal import Decimal

from django.contrib.staticfiles import finders
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.invoices.models import Invoice


class InvoiceManagementUITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            email="invoice-ui-admin@example.com", name="Invoice UI Admin",
            password="Strong-Invoice-UI-2026!",
        )
        cls.alpha = Company.objects.create(name="Alpha Billing", slug="invoice-ui-alpha")
        cls.beta = Company.objects.create(name="Beta Billing", slug="invoice-ui-beta")
        cls.customer = Customer.objects.create(company=cls.alpha, name="Alpha Customer")
        cls.other_customer = Customer.objects.create(company=cls.beta, name="Beta Customer")
        cls.unpaid = Invoice.objects.create(
            company=cls.alpha, customer=cls.customer, number="INV-100",
            kind="invoice", status="Unpaid", date=date(2026, 10, 12),
            total=Decimal("1500.00"), paid=Decimal("300.00"), balance=Decimal("1200.00"),
        )
        cls.estimate = Invoice.objects.create(
            company=cls.alpha, customer=cls.customer, number="EST-101",
            kind="estimate", status="Draft", date=date(2026, 10, 13),
            total=Decimal("900.00"), paid=Decimal("0"), balance=Decimal("0"),
        )
        cls.paid = Invoice.objects.create(
            company=cls.beta, customer=cls.other_customer, number="INV-202",
            kind="invoice", status="Paid", date=date(2026, 10, 11),
            total=Decimal("700.00"), paid=Decimal("700.00"), balance=Decimal("0"),
        )

    def setUp(self):
        self.client.force_login(self.admin)

    @staticmethod
    def payload(company_id, customer_id, *, number="INV-NEW"):
        return {
            "company": str(company_id),
            "number": number, "kind": "invoice", "invoice_type": "regular",
            "status": "Draft", "date": "2026-10-14",
            "customer": str(customer_id),
            "tax_mode": "none",
            "cgst_rate": "0.00", "sgst_rate": "0.00", "igst_rate": "0.00",
            "discount": "0.00", "adjustment": "0.00", "taxable": "500.00",
            "cgst": "0.00", "sgst": "0.00", "igst": "0.00",
            "total": "500.00", "paid": "100.00", "balance": "400.00",
        }

    def test_invoice_directory_uses_live_counts_and_correct_amounts(self):
        result = self.client.get(reverse("panel:invoices"))
        self.assertEqual(result.status_code, 200)
        self.assertContains(result, "Invoices &amp; Estimates")
        self.assertContains(result, "Invoices With Balance")
        self.assertContains(result, 'class="user-stat-value"', count=4)
        self.assertContains(result, "INV-100")
        self.assertContains(result, "EST-101")
        self.assertContains(result, "INV-202")
        self.assertContains(result, "1200.00")
        self.assertContains(result, "invoices-ui.css")
        self.assertIsNotNone(finders.find("control_panel/invoices-ui.css"))
        self.assertEqual(result.context["total_documents"], 3)
        self.assertEqual(result.context["invoice_count"], 2)
        self.assertEqual(result.context["estimate_count"], 1)
        self.assertEqual(result.context["outstanding_count"], 1)

    def test_company_kind_status_and_search_filters_combine(self):
        result = self.client.get(reverse("panel:invoices"), {
            "company": str(self.alpha.pk), "kind": "invoice",
            "status": "Unpaid", "q": "INV-100",
        })
        self.assertEqual(result.context["rows"].paginator.count, 1)
        self.assertContains(result, "INV-100")
        self.assertNotContains(result, "EST-101")
        self.assertNotContains(result, "INV-202")
        self.assertEqual(result.context["total_documents"], 3)

        estimate = self.client.get(reverse("panel:invoices"), {"kind": "estimate"})
        self.assertEqual(estimate.context["rows"].paginator.count, 1)
        self.assertContains(estimate, "EST-101")

        invalid = self.client.get(reverse("panel:invoices"), {
            "kind": "missing", "status": "not-a-status",
        })
        self.assertEqual(invalid.context["rows"].paginator.count, 3)
        self.assertEqual(invalid.context["kind"], "")
        self.assertEqual(invalid.context["status"], "")

    def test_add_edit_forms_expose_every_model_field_in_sections(self):
        result = self.client.get(reverse("panel:invoice-create"))
        self.assertTemplateUsed(result, "control_panel/invoice_form.html")
        self.assertContains(result, "Document Details")
        self.assertContains(result, "Tax Configuration")
        self.assertContains(result, "Amount &amp; Recorded Payment")
        self.assertContains(result, 'name="csrfmiddlewaretoken"')
        for field in (
            "company", "branch", "number", "kind", "invoice_type", "status",
            "date", "customer", "vehicle", "job", "tax_mode", "cgst_rate",
            "sgst_rate", "igst_rate", "payment_type", "terms_conditions",
            "staff_name", "notes", "discount", "adjustment", "taxable",
            "cgst", "sgst", "igst", "total", "paid", "balance",
            "payment_mode", "payment_terms",
        ):
            self.assertContains(result, f'name="{field}"')

        edit = self.client.get(reverse("panel:invoice-edit", args=[self.unpaid.pk]))
        self.assertTemplateUsed(edit, "control_panel/invoice_form.html")
        self.assertContains(edit, 'type="date"')
        self.assertContains(edit, 'value="2026-10-12"')
        self.assertContains(edit, "Save Changes")

    def test_create_and_edit_preserve_manually_recorded_amounts(self):
        create_url = reverse("panel:invoice-create")
        self.assertRedirects(
            self.client.post(create_url, self.payload(self.alpha.pk, self.customer.pk)),
            reverse("panel:invoices"),
        )
        document = Invoice.objects.get(company=self.alpha, number="INV-NEW")
        self.assertEqual(document.total, Decimal("500.00"))
        self.assertEqual(document.paid, Decimal("100.00"))
        self.assertEqual(document.balance, Decimal("400.00"))

        new_values = self.payload(self.alpha.pk, self.customer.pk)
        new_values["total"] = "550.00"
        new_values["balance"] = "450.00"
        self.assertRedirects(
            self.client.post(reverse("panel:invoice-edit", args=[document.pk]), new_values),
            reverse("panel:invoices"),
        )
        document.refresh_from_db()
        self.assertEqual(document.total, Decimal("550.00"))
        self.assertEqual(document.balance, Decimal("450.00"))

    def test_invalid_cross_tenant_customer_is_rejected(self):
        data = self.payload(self.alpha.pk, self.other_customer.pk, number="INV-INVALID")
        response = self.client.post(reverse("panel:invoice-create"), data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Select a record belonging to this company.")
        self.assertFalse(Invoice.objects.filter(number="INV-INVALID").exists())

    def test_details_show_original_document_without_fabricated_payment(self):
        result = self.client.get(reverse("panel:invoice-detail", args=[self.unpaid.pk]))
        self.assertContains(result, 'class="panel-card invoice-profile-header"')
        self.assertContains(result, "INV-100")
        self.assertContains(result, "Alpha Customer")
        self.assertContains(result, "1200.00")

    def test_anonymous_visitors_are_redirected(self):
        self.client.logout()
        for name in ("panel:invoices", "panel:invoice-create"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 302)
        self.assertEqual(
            self.client.get(reverse("panel:invoice-detail", args=[self.unpaid.pk])).status_code,
            302,
        )

    def test_other_create_forms_continue_to_use_existing_templates(self):
        self.assertTemplateUsed(
            self.client.get(reverse("panel:staff-create")), "control_panel/staff_form.html",
        )
