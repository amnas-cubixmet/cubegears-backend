"""E-Way Bill saved PDF produces a real tenant-safe attachment, not print HTML."""
from types import SimpleNamespace
from uuid import uuid4

from django.test import SimpleTestCase
from rest_framework.negotiation import DefaultContentNegotiation
from rest_framework.request import Request
from rest_framework.renderers import JSONRenderer
from rest_framework.test import APIRequestFactory

from .eway_pdf import create_eway_bill_pdf, eway_pdf_response
from .renderers import InvoicePDFRenderer
from .views import EWayBillViewSet


def sample_bill():
    return SimpleNamespace(
        pk=uuid4(), status="Ready for API", number="", payload={
            "documentType": "Tax Invoice", "documentNo": "INV-2026-002",
            "documentDate": "2026-10-28", "supplyType": "Outward",
            "subSupplyType": "Supply", "transactionType": "Regular",
            "from": {"tradeName": "Workshop", "gstin": "32ABCDE1234F1Z5",
                     "address": "Main Road", "pincode": "680001", "stateCode": "32"},
            "to": {"tradeName": "Customer", "gstin": "URP",
                   "address": "Customer Rd", "pincode": "680010", "stateCode": "32"},
            "items": [{
                "id": "line1", "productName": "Spare part", "hsnCode": "8708",
                "qty": 1, "unit": "PCS", "taxableValue": 100,
                "cgstRate": 9, "sgstRate": 9, "igstRate": 0, "cessRate": 0,
            }],
            "transport": {"mode": "Road", "distanceKm": "10", "vehicleNo": "KL08AB1234"},
            "notes": "Workshop goods movement",
        },
    )


class EWayBillPdfTests(SimpleTestCase):
    def test_pdf_bytes_are_real_nonempty_pdf(self):
        content = create_eway_bill_pdf(sample_bill())
        self.assertTrue(content.startswith(b"%PDF-"))
        self.assertGreater(len(content), 1000)
        self.assertIn(b"%%EOF", content[-100:])

    def test_filename_headers_and_local_issuance_are_explicit(self):
        result = eway_pdf_response(sample_bill())
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result["Content-Type"], "application/pdf")
        self.assertIn("EWayBill-INV-2026-002.pdf", result["Content-Disposition"])
        self.assertEqual(result["Cache-Control"], "private, no-store")
        self.assertEqual(result["X-Content-Type-Options"], "nosniff")

    def test_pdf_escapes_customer_content(self):
        bill = sample_bill()
        bill.payload["notes"] = '<script>alert("bad")</script>'
        self.assertTrue(create_eway_bill_pdf(bill).startswith(b"%PDF-"))

    def test_export_accept_negotiation_supports_pdf_and_json(self):
        action = EWayBillViewSet.pdf
        self.assertEqual(action.kwargs["renderer_classes"], [JSONRenderer, InvoicePDFRenderer])
        request = Request(APIRequestFactory().get(
            "/api/v1/e-way-bills/test-id/pdf", HTTP_ACCEPT="application/pdf"))
        renderers = [cls() for cls in action.kwargs["renderer_classes"]]
        chosen, _ = DefaultContentNegotiation().select_renderer(request, renderers)
        self.assertIsInstance(chosen, InvoicePDFRenderer)
        self.assertEqual(EWayBillViewSet.action_permission_map["pdf"], "invoices.view")
