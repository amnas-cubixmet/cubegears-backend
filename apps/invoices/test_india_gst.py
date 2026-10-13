from decimal import Decimal
from django.test import TestCase
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.companies.models import Company
from apps.branches.models import Branch
from apps.roles.models import Role
from .models import Invoice


class InvoiceIndiaGstTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Billing GST workshop", slug="billing-gst-test",
            gstin="32ABCDE1234F1Z5", state="Kerala",
        )
        branch = Branch.objects.create(
            company=self.company, name="Main", code="MAIN", state="Kerala"
        )
        role = Role.objects.create(
            company=self.company, code="INVOICE", name="Billing Clerk",
            permissions=["invoices.view", "invoices.create", "invoices.edit", "stock.edit"],
        )
        user = User.objects.create(
            company=self.company, branch=branch, role=role,
            name="Invoice Clerk", email="billing-gst@test.local",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=user)
        self.url = "/api/v1/billing/documents"

    def payload(self, pos="32", **updates):
        return {
            "invoiceType": "gst",
            "kind": "invoice",
            "date": "2026-10-13",
            "customer": {
                "name": "GST Customer", "phone": "9876501234",
                "state": "Kerala", "placeOfSupply": pos,
            },
            "items": [{
                "type": "Labour", "description": "Car repair labour",
                "qty": "1", "rate": "1000", "taxRate": "18",
                "hsnCode": "998714",
            }],
            **updates,
        }

    def test_intra_state_line_gst_is_cgst_plus_sgst(self):
        data = self.client.post(self.url, self.payload(), format="json")
        self.assertEqual(data.status_code, 201, data.data)
        self.assertEqual(data.data["invoiceType"], "gst")
        self.assertEqual(data.data["taxMode"], "cgst_sgst")
        self.assertEqual(Decimal(data.data["cgst"]), Decimal("90"))
        self.assertEqual(Decimal(data.data["sgst"]), Decimal("90"))
        self.assertEqual(Decimal(data.data["igst"]), Decimal("0"))
        self.assertEqual(Decimal(data.data["total"]), Decimal("1180"))

    def test_inter_state_is_igst(self):
        data = self.client.post(self.url, self.payload(pos="33"), format="json")
        self.assertEqual(data.status_code, 201, data.data)
        self.assertEqual(data.data["taxMode"], "igst")
        self.assertEqual(Decimal(data.data["igst"]), Decimal("180"))
        self.assertEqual(Decimal(data.data["cgst"]), Decimal("0"))
        self.assertEqual(Decimal(data.data["sgst"]), Decimal("0"))

    def test_discount_and_client_supplied_fake_tax_are_ignored(self):
        body = self.payload(discount="50", cgst="9000", igst="9000",
                            taxMode="igst")
        body["items"][0]["discount"] = "50"
        data = self.client.post(self.url, body, format="json")
        self.assertEqual(data.status_code, 201, data.data)
        self.assertEqual(Decimal(data.data["taxable"]), Decimal("900"))
        self.assertEqual(Decimal(data.data["cgst"]), Decimal("81"))
        self.assertEqual(Decimal(data.data["sgst"]), Decimal("81"))
        self.assertEqual(Decimal(data.data["igst"]), Decimal("0"))
        self.assertEqual(Decimal(data.data["total"]), Decimal("1062"))

    def test_missing_place_of_supply_rejected_without_orphan_record(self):
        body = self.payload(pos="")
        body["customer"]["state"] = ""
        data = self.client.post(self.url, body, format="json")
        self.assertEqual(data.status_code, 400, data.data)
        self.assertEqual(Invoice.objects.filter(company=self.company).count(), 0)

    def test_gst_settings_are_available_to_billing_clerk(self):
        settings = self.client.get(self.url + "/gst-context")
        self.assertEqual(settings.status_code, 200, settings.data)
        self.assertTrue(settings.data["registered"])
        self.assertEqual(settings.data["supplierState"], "32")
