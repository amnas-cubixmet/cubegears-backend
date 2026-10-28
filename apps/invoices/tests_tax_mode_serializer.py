"""Regression: taxMode is not an editable invoice input."""
from types import SimpleNamespace
from django.test import SimpleTestCase

from .serializers import InvoiceSerializer


class InvoiceTaxModeInputTests(SimpleTestCase):
    def _serializer(self, gstin, supplied):
        company = SimpleNamespace(gstin=gstin)
        request = SimpleNamespace(user=SimpleNamespace(company=company))
        serializer = InvoiceSerializer(
            data={"taxMode": supplied}, partial=True, context={"request": request},
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        return serializer

    def test_blank_taxmode_is_ignored_for_new_gst_invoice(self):
        serializer = self._serializer("32ABCDE1234F1Z5", "")
        self.assertNotIn("tax_mode", serializer.validated_data)

    def test_client_cannot_override_tax_mode_with_igst_or_none(self):
        for mode in ("igst", "cgst_sgst", "none", ""):
            with self.subTest(mode=mode):
                serializer = self._serializer("32ABCDE1234F1Z5", mode)
                self.assertNotIn("tax_mode", serializer.validated_data)

    def test_normal_invoice_blank_mode_is_ignored(self):
        serializer = self._serializer("", "")
        self.assertNotIn("tax_mode", serializer.validated_data)
