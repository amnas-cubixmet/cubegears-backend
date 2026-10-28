"""Blank optional invoiceId must not prevent manual E-Way Bill draft creation."""
from types import SimpleNamespace
from uuid import UUID
from django.test import SimpleTestCase

from apps.invoices.serializers import EWayBillSerializer


class EWayBillOptionalInvoiceReferenceTests(SimpleTestCase):
    def setUp(self):
        company = SimpleNamespace(
            name="Example Garage", gstin="32ABCDE1234F1Z5",
            address="Main Road", city="Thrissur",
            pincode="680001", state="Kerala",
        )
        self.context = {"request": SimpleNamespace(
            user=SimpleNamespace(company=company, branch=None),
        )}
        self.payload = {
            "supplyType": "Outward",
            "to": {"tradeName": "Customer"},
            "transport": {"mode": "Road"},
        }

    def test_blank_optional_invoice_id_accepted_as_null(self):
        serializer = EWayBillSerializer(
            data={**self.payload, "invoiceId": ""},
            context=self.context,
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertIsNone(serializer.validated_data["invoice_id"])
        self.assertNotIn("invoiceId", serializer.validated_data["payload"])

    def test_omitted_invoice_id_is_allowed(self):
        serializer = EWayBillSerializer(data=self.payload, context=self.context)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertNotIn("invoice_id", serializer.validated_data)

    def test_actual_invoice_uuid_still_parsed(self):
        value = "b08de93a-e83a-4357-b1e3-3306914ad8bb"
        serializer = EWayBillSerializer(
            data={**self.payload, "invoiceId": value},
            context=self.context,
        )
        attrs = serializer.to_internal_value({**self.payload, "invoiceId": value})
        self.assertEqual(attrs["invoice_id"], UUID(value))

    def test_malformed_nonblank_invoice_id_rejected(self):
        serializer = EWayBillSerializer(data={
            **self.payload, "invoiceId": "INV-2026-0001",
        }, context=self.context)
        self.assertFalse(serializer.is_valid())
        self.assertIn("invoiceId", serializer.errors)
