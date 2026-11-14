"""Contact phone normalization for workshop Customers and Suppliers."""
from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError

from common.phone_numbers import normalize_contact_phone
from apps.customers.serializers import CustomerSerializer


class ContactPhoneTests(SimpleTestCase):
    def test_supported_country_codes_use_exact_national_length(self):
        samples = {
            "+91": "9876543210",
            "+971": "501234567",
            "+966": "501234567",
            "+974": "55123456",
            "+965": "55123456",
            "+968": "92123456",
            "+973": "33123456",
            "+1": "2025550198",
            "+44": "7123456789",
            "+61": "412345678",
        }
        for code, national in samples.items():
            with self.subTest(country=code):
                self.assertEqual(
                    normalize_contact_phone(code + national),
                    code + national,
                )
                with self.assertRaises(ValidationError):
                    normalize_contact_phone(code + national[:-1])
                with self.assertRaises(ValidationError):
                    normalize_contact_phone(code + national + "0")

    def test_india_local_and_international_paste(self):
        self.assertEqual(normalize_contact_phone("9876543210"), "+919876543210")
        self.assertEqual(normalize_contact_phone("0091 98765 43210"), "+919876543210")
        self.assertEqual(normalize_contact_phone("+91-98765 43210"), "+919876543210")

    def test_optional_supplier_and_required_customer(self):
        self.assertEqual(normalize_contact_phone("", required=False), "")
        with self.assertRaises(ValidationError):
            normalize_contact_phone("", required=True)
        with self.assertRaises(ValidationError):
            normalize_contact_phone("+91invalid")

    def test_customer_phone_also_populates_whatsapp(self):
        serializer = CustomerSerializer(data={
            "name": "Workshop Customer",
            "phone": "+919876543210",
            "whatsapp": "+971501234567",
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data["phone"], "+919876543210")
        self.assertEqual(serializer.validated_data["whatsapp"], "+919876543210")
