"""GSTIN validation regression tests for supplier CRUD and GST purchases."""
from types import SimpleNamespace
from decimal import Decimal

from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.inventory.models import Supplier
from apps.roles.models import Role
from apps.suppliers.gstin import (
    normalized_supplier_gstin, supplier_gstin_error, supplier_gstin_state_code,
)
from apps.suppliers.purchase_gst import purchase_gst_context, purchase_line_components


class SupplierGstinValidationTests(SimpleTestCase):
    def test_valid_state_and_format(self):
        self.assertEqual(normalized_supplier_gstin(" 32abcde1234f1z5 "), "32ABCDE1234F1Z5")
        self.assertEqual(supplier_gstin_error("32ABCDE1234F1Z5"), "")
        self.assertEqual(supplier_gstin_state_code("32ABCDE1234F1Z5"), "32")

    def test_blank_is_unregistered(self):
        self.assertEqual(supplier_gstin_error(""), "")
        self.assertEqual(supplier_gstin_state_code(""), "")
        result = purchase_gst_context(SimpleNamespace(gstin=""), "32")
        self.assertEqual(result["taxMode"], "unregistered")

    def test_unregistered_purchase_requires_neither_buyer_state_nor_extra_tax_classification(self):
        context = purchase_gst_context(SimpleNamespace(gstin=""), None)
        self.assertEqual(context["taxMode"], "unregistered")
        self.assertEqual(context["placeOfSupply"], "")
        result = purchase_line_components(Decimal("100"), Decimal("0"), context, "")
        self.assertEqual(result["tax"], Decimal("0"))
        self.assertEqual(result["gstTreatment"], "unregistered")
        with self.assertRaises(ValidationError):
            purchase_line_components(Decimal("100"), Decimal("18"), context, "taxable")

    def test_registered_supplier_uses_buyer_state_and_preserves_legacy_clients(self):
        registered = SimpleNamespace(gstin="32ABCDE1234F1Z5")
        self.assertEqual(purchase_gst_context(registered, None)["taxMode"], "legacy")
        self.assertEqual(purchase_gst_context(registered, "32")["taxMode"], "cgst_sgst")
        self.assertEqual(purchase_gst_context(registered, "29")["taxMode"], "igst")

    def test_incomplete_wrong_state_and_wrong_structure_are_rejected(self):
        cases = (
            "Kerala GSTIN", "ABABCDE1234F1Z5", "00ABCDE1234F1Z5",
            "32ABCDE1234F1Z", "32ABCDE1234F1Z50", "32ABCDE1234F1Y5",
            "32ABCDE1234FZ15", "29ABCDE1234F1Z-", "99ABCDE1234F1Z5",
        )
        for value in cases:
            with self.subTest(value=value):
                self.assertTrue(supplier_gstin_error(value))
                self.assertEqual(supplier_gstin_state_code(value), "")
                with self.assertRaises(ValidationError):
                    purchase_gst_context(SimpleNamespace(gstin=value), "32")

    def test_supplier_state_determines_tax_jurisdiction(self):
        same = purchase_gst_context(SimpleNamespace(gstin="32ABCDE1234F1Z5"), "32")
        other = purchase_gst_context(SimpleNamespace(gstin="29ABCDE1234F1Z5"), "32")
        self.assertEqual(same["taxMode"], "cgst_sgst")
        self.assertEqual(other["taxMode"], "igst")


@override_settings(
    CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
    CREDIT_LABEL_ONLY_ACCESS_ENABLED=False,
)
class SupplierGstinAPITests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="GSTIN Test Workshop", slug="gstin-test-workshop")
        branch = Branch.objects.create(company=self.company, name="Main", code="MAIN", is_active=True)
        role = Role.objects.create(
            company=self.company, name="GSTIN Owner", code="GSTIN_OWNER",
            permissions=["*"], is_active=True, is_system=True,
        )
        self.user = User.objects.create_user(
            email="gstin-tests@example.test", password="test-only",
            name="Owner", phone="+919999000003", company=self.company,
            branch=branch, role=role,
        )
        self.company.owner = self.user
        self.company.save(update_fields=["owner"])
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_invalid_gstin_rejected_on_supplier_create(self):
        response = self.client.post(
            "/api/v1/stock/suppliers",
            {"name": "Bad GST Shop", "gstNo": "Kerala"},
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("gstNo", response.data)
        self.assertFalse(Supplier.objects.filter(company=self.company, name="Bad GST Shop").exists())

    def test_unregistered_supplier_can_be_saved_without_gstin(self):
        response = self.client.post(
            "/api/v1/stock/suppliers",
            {"name": "Unregistered Store", "gstNo": ""},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Supplier.objects.get(pk=response.data["id"]).gstin, "")

    def test_valid_gstin_is_normalized_and_saved(self):
        response = self.client.post(
            "/api/v1/stock/suppliers",
            {"name": "Registered Supplier", "gstNo": " 32abcde1234f1z5 "},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Supplier.objects.get(pk=response.data["id"]).gstin, "32ABCDE1234F1Z5")

    def test_edit_can_fix_old_bad_gstin_without_affecting_old_bills(self):
        supplier = Supplier.objects.create(
            company=self.company, name="Legacy Supplier", gstin="KERALA",
        )
        url = f"/api/v1/stock/suppliers/{supplier.pk}"
        # An unrelated patch must continue working for old stored data.
        patch = self.client.patch(url, {"address": "Main Road"}, format="json")
        self.assertEqual(patch.status_code, 200, patch.data)

        invalid = self.client.patch(url, {"gstNo": "KERALA"}, format="json")
        self.assertEqual(invalid.status_code, 400, invalid.data)
        self.assertIn("gstNo", invalid.data)

        fixed = self.client.patch(url, {"gstNo": "32ABCDE1234F1Z5"}, format="json")
        self.assertEqual(fixed.status_code, 200, fixed.data)
        supplier.refresh_from_db()
        self.assertEqual(supplier.gstin, "32ABCDE1234F1Z5")
