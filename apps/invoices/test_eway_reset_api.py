from datetime import date

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.roles.models import Role
from .models import EWayBill, Invoice


class EWayBillResetApiTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="EWay Workshop", slug="eway-reset-test", gstin="32ABCDE1234F1Z5",
            state="Kerala",
        )
        self.other = Company.objects.create(name="Another Workshop", slug="eway-reset-other")
        role = Role.objects.create(
            company=self.company, name="Billing", code="BILLING",
            permissions=["invoices.view", "invoices.create", "invoices.edit", "invoices.delete"],
        )
        user = User.objects.create(
            company=self.company, role=role, name="Billing Manager", email="eway@test.local",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=user)
        self.base = "/api/v1/billing/e-way-bills"

    def draft(self, **extra):
        return {
            "status": "Generated", "ewayBillNo": "123456789012",
            "supplyType": "Outward", "subSupplyType": "Supply",
            "transactionType": "Regular", "documentType": "Tax Invoice",
            "documentNo": "INV-2001", "documentDate": "2026-10-13",
            "from": {
                "gstin": "32ABCDE1234F1Z5", "tradeName": "EWay Workshop",
                "address": "Workshop Road", "place": "Malappuram",
                "pincode": "676505", "stateCode": "32",
            },
            "to": {
                "gstin": "URP", "tradeName": "Parts Buyer",
                "address": "Parts Street", "place": "Kochi",
                "pincode": "682001", "stateCode": "32",
            },
            "items": [{
                "id": "line-a", "productName": "Brake pad", "hsnCode": "8708",
                "qty": "2", "unit": "PCS", "taxableValue": "800.00",
                "cgstRate": "9", "sgstRate": "9", "igstRate": "0",
            }],
            "transport": {
                "mode": "Road", "distanceKm": "160", "vehicleNo": "KL10AB1234",
            },
            **extra,
        }

    def test_rejects_fake_generated_status_number_and_qr(self):
        body = self.draft(qrCodeDataUrl="data:fake", generatedAt="2026-10-13")
        result = self.client.post(self.base, body, format="json")
        self.assertEqual(result.status_code, 201, result.data)
        self.assertEqual(result.data["status"], "Draft")
        self.assertEqual(result.data["ewayBillNo"], "")
        self.assertNotIn("qrCodeDataUrl", result.data)
        obj = EWayBill.objects.get(pk=result.data["id"])
        self.assertEqual(obj.number, "")
        self.assertNotIn("generatedAt", obj.payload)

    def test_prepare_reset_edit_and_delete_local_draft(self):
        created = self.client.post(self.base, self.draft(), format="json")
        self.assertEqual(created.status_code, 201, created.data)
        bill_id = created.data["id"]
        ready = self.client.post(f"{self.base}/{bill_id}/generate")
        self.assertEqual(ready.status_code, 200, ready.data)
        self.assertEqual(ready.data["status"], "Ready for API")
        self.assertEqual(ready.data["ewayBillNo"], "")
        self.assertEqual(self.client.patch(
            f"{self.base}/{bill_id}", {"notes": "new"}, format="json"
        ).status_code, 400)
        self.assertEqual(self.client.delete(f"{self.base}/{bill_id}").status_code, 400)
        reset = self.client.post(f"{self.base}/{bill_id}/reset-draft")
        self.assertEqual(reset.status_code, 200, reset.data)
        self.assertEqual(reset.data["status"], "Draft")
        edited = self.client.patch(
            f"{self.base}/{bill_id}", {"notes": "edited"}, format="json"
        )
        self.assertEqual(edited.status_code, 200, edited.data)
        self.assertEqual(edited.data["notes"], "edited")
        self.assertEqual(self.client.delete(f"{self.base}/{bill_id}").status_code, 204)

    def test_validation_refuses_missing_goods_and_mixed_tax_modes(self):
        itemless = self.client.post(self.base, self.draft(items=[]), format="json")
        self.assertEqual(itemless.status_code, 201, itemless.data)
        result = self.client.post(f"{self.base}/{itemless.data['id']}/generate")
        self.assertEqual(result.status_code, 400, result.data)

        conflict = self.draft()
        conflict["items"][0]["igstRate"] = "18"
        created = self.client.post(self.base, conflict, format="json")
        response = self.client.post(f"{self.base}/{created.data['id']}/generate")
        self.assertEqual(response.status_code, 400, response.data)

    def test_cannot_reset_portal_issued_bill_or_another_tenant(self):
        official = EWayBill.objects.create(
            company=self.company, number="234567890123", status="Generated",
        )
        url = f"{self.base}/{official.pk}"
        self.assertEqual(self.client.post(url + "/reset-draft").status_code, 400)
        self.assertEqual(self.client.delete(url).status_code, 400)
        self.assertEqual(self.client.post(url + "/generate").status_code, 400)
        separate = EWayBill.objects.create(company=self.other)
        self.assertEqual(self.client.get(f"{self.base}/{separate.pk}").status_code, 404)
        self.assertEqual(self.client.post(
            f"{self.base}/{separate.pk}/reset-draft"
        ).status_code, 404)

    def test_related_invoice_must_be_in_same_workshop(self):
        customer = Customer.objects.create(
            company=self.other, name="Other Client", phone="9999999999"
        )
        invoice = Invoice.objects.create(
            company=self.other, customer=customer,
            number="INV-OTHER-1", date=date(2026, 10, 13)
        )
        response = self.client.post(
            self.base, self.draft(invoiceId=str(invoice.pk)), format="json"
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(EWayBill.objects.filter(company=self.company).count(), 0)
