"""Invoice PDF must work with Accept: application/pdf without returning DRF 406."""
import json
from types import SimpleNamespace
from unittest.mock import patch

from django.http import HttpResponse
from django.test import SimpleTestCase
from rest_framework.negotiation import DefaultContentNegotiation
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from .pdf import pdf_response
from .renderers import InvoicePDFRenderer
from .views import InvoiceViewSet


class InvoicePDFContentNegotiationTests(SimpleTestCase):
    def _negotiate(self, accept):
        action = InvoiceViewSet.pdf
        renderers = [klass() for klass in action.kwargs["renderer_classes"]]
        request = Request(APIRequestFactory().get("/api/v1/billing/documents/id/pdf",
                                                  HTTP_ACCEPT=accept))
        return DefaultContentNegotiation().select_renderer(request, renderers)[0]

    def test_pdf_accept_selects_binary_renderer_instead_of_406(self):
        self.assertIsInstance(self._negotiate("application/pdf"), InvoicePDFRenderer)

    def test_default_and_json_accept_remain_supported(self):
        self.assertEqual(self._negotiate("application/json").media_type, "application/json")
        self.assertEqual(self._negotiate("*/*").media_type, "application/json")

    def test_pdf_renderer_produces_raw_pdf_bytes(self):
        pdf = b"%PDF-1.4\nexample\n"
        self.assertEqual(InvoicePDFRenderer().render(pdf), pdf)

    def test_pdf_renderer_keeps_permission_errors_json(self):
        response = HttpResponse(status=403)
        data = {"detail": "You do not have permission."}
        result = InvoicePDFRenderer().render(data, renderer_context={"response": response})
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(json.loads(result), data)

    def test_successful_pdf_response_retains_attachment_security_headers(self):
        saved = SimpleNamespace(number="INV-2026-0001")
        with patch("apps.invoices.pdf.create_invoice_pdf", return_value=b"%PDF-1.4\n"):
            response = pdf_response(saved)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertIn("attachment;", response["Content-Disposition"])
        self.assertTrue(response.content.startswith(b"%PDF-"))
