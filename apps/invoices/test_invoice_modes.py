from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.companies.models import Company
from apps.roles.models import Role
from .models import Invoice


class CompanyInvoiceModesTests(TestCase):
    def setUp(self):
        self.registered = Company.objects.create(
            name="GST Garage", slug="invoice-mode-gst-garage",
            gstin="32ABCDE1234F1Z5", state="Kerala",
        )
        self.unregistered = Company.objects.create(
            name="Small Garage", slug="invoice-mode-no-gst",
            gstin="", state="Kerala",
        )
        self.users = {}
        for label, company in (("gst", self.registered), ("regular", self.unregistered)):
            role = Role.objects.create(
                company=company, name="Invoice Clerk", code="INVOICE",
                permissions=["invoices.create", "invoices.view", "invoices.edit", "stock.edit"],
            )
            self.users[label] = User.objects.create(
                company=company, role=role, name="Clerk",
                email=f"invoice-mode-{label}@example.test",
            )
        self.client = APIClient()
        self.url = "/api/v1/billing/documents"

    def login(self, label):
        self.client.force_authenticate(user=self.users[label])

    def data(self, **updates):
        return {
            "kind": "invoice",
            "date": "2026-10-13",
            "customer": {"name": "Walk-in Customer", "phone": "1234500011",
                         "state": "Kerala", "placeOfSupply": "32"},
            "items": [
                {"type": "Labour", "description": "Engine servicing",
                 "qty": "1", "rate": "1000", "taxRate": "18", "hsnCode": "998714"},
                {"type": "Stock Part", "description": "Oil filter",
                 "qty": "1", "rate": "2000", "taxRate": "18", "hsnCode": "84212300"},
            ],
            **updates,
        }

    def test_gst_registered_workshop_creates_tax_invoice(self):
        self.login("gst")
        res = self.client.post(self.url, self.data(), format="json")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["invoiceType"], "gst")
        self.assertEqual(Decimal(res.data["total"]), Decimal("3540"))
        self.assertEqual(Decimal(res.data["cgst"]), Decimal("270"))
        self.assertEqual(Decimal(res.data["sgst"]), Decimal("270"))
        self.assertEqual(res.data["seller"]["gstin"], self.registered.gstin)

    def test_unregistered_workshop_creates_normal_invoice_without_gst(self):
        self.login("regular")
        res = self.client.post(
            self.url, self.data(taxMode="igst", cgst="1500", igst="1900"),
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["invoiceType"], "regular")
        self.assertEqual(res.data["taxMode"], "none")
        self.assertEqual(Decimal(res.data["cgst"]), Decimal("0"))
        self.assertEqual(Decimal(res.data["sgst"]), Decimal("0"))
        self.assertEqual(Decimal(res.data["igst"]), Decimal("0"))
        self.assertEqual(Decimal(res.data["total"]), Decimal("3000"))
        self.assertEqual(res.data["seller"]["gstin"], "")
        self.assertEqual(self.client.post(
            f"{self.url}/{res.data['id']}/finalize"
        ).status_code, 200)

    def test_unregistered_business_cannot_issue_gst_invoice(self):
        self.login("regular")
        res = self.client.post(
            self.url, self.data(invoiceType="gst"), format="json"
        )
        self.assertEqual(res.status_code, 400, res.data)
        self.assertIn("invoiceType", res.data)

    def test_gst_registered_business_cannot_bypass_tax_with_normal_invoice(self):
        self.login("gst")
        res = self.client.post(
            self.url, self.data(invoiceType="regular"), format="json"
        )
        self.assertEqual(res.status_code, 400, res.data)
        self.assertIn("invoiceType", res.data)

    def test_customer_gstin_is_not_required_for_registered_seller(self):
        self.login("gst")
        res = self.client.post(
            self.url, self.data(customer={
                "name": "Walk-in", "phone": "1234500012",
                "placeOfSupply": "32", "gstin": "",
            }), format="json",
        )
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["invoiceType"], "gst")

    def test_seller_gstin_cannot_be_faked_by_api_payload(self):
        self.login("regular")
        res = self.client.post(
            self.url, self.data(seller={"name": "Fake GST Garage",
                                        "gstin": "32ABCDE1234F1Z5"}),
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["seller"]["gstin"], "")
        self.assertEqual(res.data["seller"]["name"], "Small Garage")

    def test_context_drives_correct_invoice_type(self):
        for label, expected in (("gst", "gst"), ("regular", "regular")):
            self.login(label)
            res = self.client.get(self.url + "/gst-context")
            self.assertEqual(res.status_code, 200, res.data)
            self.assertEqual(res.data["invoiceType"], expected)
            self.assertEqual(res.data["registered"], expected == "gst")
