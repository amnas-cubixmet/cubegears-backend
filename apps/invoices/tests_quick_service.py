from decimal import Decimal
from uuid import uuid4

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.inventory.models import WorkshopAccountEntry, WorkshopFinancialAccount
from apps.payments.models import Payment
from apps.roles.models import Role
from apps.vehicles.models import Vehicle
from .models import Invoice, QuickService


@override_settings(CREDIT_FEATURE_ENFORCEMENT_ENABLED=False, CREDIT_LABEL_ONLY_ACCESS_ENABLED=False)
class QuickServiceTests(TestCase):
    url = "/api/v1/invoices/quick-services"

    def setUp(self):
        self.company = Company.objects.create(name="Quick Workshop", slug="quick-workshop", state="Kerala")
        self.branch = Branch.objects.create(company=self.company, name="Main", code="MAIN", state="Kerala")
        self.role = Role.objects.create(company=self.company, name="Workshop", code="QUICK", permissions=["*"])
        self.user = User.objects.create_user(
            email="quick@example.test", password="test-password", name="Counter",
            company=self.company, branch=self.branch, role=self.role,
        )
        self.company.owner = self.user
        self.company.save(update_fields=["owner"])
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.cash = WorkshopFinancialAccount.objects.create(company=self.company, branch=self.branch, name="Cash drawer", kind="Cash")
        self.bank = WorkshopFinancialAccount.objects.create(company=self.company, branch=self.branch, name="UPI account", kind="UPI")
        self.items = [{"description": "Tyre puncture repair", "qty": "2", "rate": "150.00"}]

    def start(self):
        payload = {"description": "Tyre puncture repair", "requestId": str(uuid4())}
        result = self.client.post(self.url, payload, format="json")
        self.assertEqual(result.status_code, 201, result.data)
        self.assertEqual(self.client.post(self.url, payload, format="json").status_code, 200)
        self.assertIsNone(result.data["invoiceId"])
        self.assertFalse(Invoice.objects.exists())
        return result.data["id"]

    def checkout(self, service, *, method="Cash", account=None, total="300.00"):
        return self.client.post(f"{self.url}/{service}/close", {
            "items": self.items, "paymentMethod": method, "account": str((account or self.cash).pk),
            "expectedTotal": total,
        }, format="json")

    def test_cash_closes_and_posts_once_without_customer_vehicle_or_job(self):
        service = self.start()
        quote = self.client.post(f"{self.url}/{service}/preview", {"items": self.items}, format="json")
        self.assertEqual(quote.status_code, 200, quote.data)
        self.assertEqual(quote.data["total"], "300.00")
        self.assertFalse(Invoice.objects.exists())
        self.assertEqual(self.checkout(service, total="299.99").status_code, 400)
        result = self.checkout(service)
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data["invoiceStatus"], "Paid")
        invoice = Invoice.objects.get()
        self.assertEqual(result.data["invoiceId"], str(invoice.pk))
        self.assertEqual(invoice.quick_service_id, QuickService.objects.get().pk)
        self.assertEqual(invoice.paid, Decimal("300.00"))
        self.assertEqual(invoice.balance, 0)
        self.assertIsNone(invoice.customer_id)
        self.assertIsNone(invoice.vehicle_id)
        self.assertIsNone(invoice.job_id)
        self.assertFalse(Customer.objects.exists())
        self.assertFalse(Vehicle.objects.exists())
        self.assertEqual(self.checkout(service).data, result.data)
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(WorkshopAccountEntry.objects.get(account=self.cash).amount, Decimal("300.00"))
        detail = self.client.get(f"/api/v1/invoices/{invoice.pk}")
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertEqual(detail.data["customer"], {})
        pdf = self.client.get(f"/api/v1/invoices/{invoice.pk}/pdf", HTTP_ACCEPT="application/pdf")
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b"%PDF"))

    def test_checkout_rolls_back_on_invalid_channel_and_bank_retry_works(self):
        service = self.start()
        failed = self.checkout(service, method="Cash", account=self.bank)
        self.assertEqual(failed.status_code, 400, failed.data)
        self.assertFalse(Invoice.objects.exists())
        self.assertFalse(Payment.objects.exists())
        self.assertFalse(WorkshopAccountEntry.objects.exists())
        result = self.checkout(service, method="Bank", account=self.bank)
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(Payment.objects.get().account, self.bank)

    def test_unregistered_customer_gst_invoice_uses_branch_tax_and_checks_recipient_limit(self):
        self.company.gstin = "32AAIFF2342J1ZO"
        self.company.save(update_fields=["gstin"])
        service = self.start()
        self.assertEqual(self.checkout(service).status_code, 400)
        self.items[0].update(hsnCode="998729", taxRate="18.00", rate="25000.00")
        self.assertEqual(self.checkout(service, total="59000.00").status_code, 400)
        self.items[0]["rate"] = "150.00"
        result = self.checkout(service, total="354.00")
        self.assertEqual(result.status_code, 200, result.data)
        invoice = Invoice.objects.get()
        self.assertEqual((invoice.cgst, invoice.sgst, invoice.igst), (Decimal("27.00"), Decimal("27.00"), 0))
        self.assertEqual(invoice.customer_snapshot, {"placeOfSupply": "32"})

    def test_registered_invoice_path_still_requires_a_customer(self):
        result = self.client.post("/api/v1/invoices", {
            "date": "2026-11-19", "items": self.items, "quickServiceId": str(uuid4()),
        }, format="json")
        self.assertEqual(result.status_code, 400, result.data)
        self.assertFalse(Invoice.objects.exists())

    def test_checkout_requires_billing_permissions_and_scopes_service_and_account(self):
        service = self.start()
        limited = Role.objects.create(company=self.company, name="Mechanic", code="MECH", permissions=["jobs.view", "jobs.edit"])
        worker = User.objects.create_user(email="mechanic@example.test", name="Mechanic", company=self.company, role=limited)
        self.client.force_authenticate(user=worker)
        self.assertEqual(self.checkout(service).status_code, 403)

        other = Company.objects.create(name="Another Workshop", slug="another-workshop")
        other_role = Role.objects.create(company=other, name="Owner", code="OTHER", permissions=["*"])
        other_user = User.objects.create_user(email="other@example.test", name="Owner", company=other, role=other_role)
        other.owner = other_user
        other.save(update_fields=["owner"])
        foreign = WorkshopFinancialAccount.objects.create(company=other, name="Foreign cash", kind="Cash")
        self.client.force_authenticate(user=other_user)
        self.assertEqual(self.checkout(service, account=foreign).status_code, 404)
        self.client.force_authenticate(user=self.user)
        self.assertEqual(self.checkout(service, account=foreign).status_code, 400)
        self.assertFalse(Invoice.objects.exists())
