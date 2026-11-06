"""Regression coverage for the canonical suppliers app and legacy inventory URLs."""
from django.apps import apps
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import resolve
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.inventory import supplier_finance_api as legacy_finance
from apps.inventory.models import Supplier, SupplierBill
from apps.roles.models import Role
from apps.suppliers import finance_api
from apps.suppliers.models import Supplier as SuppliersDomainModel
from apps.suppliers.views import SupplierViewSet


class SupplierRoutingTests(SimpleTestCase):
    def test_dedicated_app_owns_suppliers_and_legacy_routes_remain_aliases(self):
        self.assertEqual(apps.get_app_config("suppliers").name, "apps.suppliers")
        self.assertIs(resolve("/api/v1/suppliers").func.cls, SupplierViewSet)
        self.assertIs(resolve("/api/v1/suppliers/").func.cls, SupplierViewSet)
        self.assertIs(resolve("/api/v1/stock/suppliers").func.cls, SupplierViewSet)
        self.assertIs(resolve("/api/v1/inventory/suppliers").func.cls, SupplierViewSet)
        self.assertEqual(SupplierViewSet.permission_prefix, "stock")

    def test_financial_paths_are_one_canonical_implementation(self):
        pairs = (
            ("/api/v1/suppliers/procurement/accounts", "/api/v1/stock/procurement/accounts", finance_api.AccountCollection),
            ("/api/v1/suppliers/procurement/bills", "/api/v1/inventory/procurement/bills", finance_api.SupplierBillCollection),
            ("/api/v1/suppliers/procurement/payments", "/api/v1/stock/procurement/payments", finance_api.SupplierPaymentCollection),
        )
        for canonical, legacy, view in pairs:
            with self.subTest(canonical=canonical):
                self.assertIs(resolve(canonical).func.view_class, view)
                self.assertIs(resolve(legacy).func.view_class, view)
        self.assertIs(legacy_finance.SupplierSummary, finance_api.SupplierSummary)
        self.assertIs(legacy_finance.SupplierLedger, finance_api.SupplierLedger)

    def test_existing_models_are_not_recreated_under_new_app(self):
        self.assertIs(Supplier, SuppliersDomainModel)
        self.assertIs(apps.get_model("inventory", "Supplier"), Supplier)
        with self.assertRaises(LookupError):
            apps.get_model("suppliers", "Supplier")


@override_settings(
    CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
    CREDIT_LABEL_ONLY_ACCESS_ENABLED=False,
)
class SupplierCompatibilityTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Supplier App Test Garage", slug="supplier-app-test-garage",
        )
        self.branch = Branch.objects.create(
            company=self.company, name="Main", code="M", is_active=True,
        )
        self.role = Role.objects.create(
            company=self.company, name="Owner", code="SUPPLIERS_OWNER",
            permissions=["*"], is_active=True, is_system=True,
        )
        self.user = User.objects.create_user(
            email="supplier-app-owner@example.test",
            password="sufficient-password",
            name="Owner", phone="+919999000002", company=self.company,
            branch=self.branch, role=self.role,
        )
        self.company.owner = self.user
        self.company.save(update_fields=["owner"])
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_create_in_new_app_read_in_legacy_inventory_and_deactivate(self):
        create = self.client.post("/api/v1/suppliers", {
            "name": "Compatible Parts Ltd", "phone": "+919876543210",
        }, format="json")
        self.assertEqual(create.status_code, 201, getattr(create, "data", None))
        supplier_id = create.data["id"]
        old = self.client.get(f"/api/v1/stock/suppliers/{supplier_id}")
        self.assertEqual(old.status_code, 200)
        self.assertEqual(old.data["name"], "Compatible Parts Ltd")
        self.assertEqual(Supplier.objects.filter(company=self.company).count(), 1)

        updated = self.client.patch(f"/api/v1/suppliers/{supplier_id}", {
            "is_active": False,
        }, format="json")
        self.assertEqual(updated.status_code, 200)
        self.assertFalse(Supplier.objects.get(pk=supplier_id).is_active)

    def test_cannot_delete_supplier_with_historical_bill(self):
        supplier = Supplier.objects.create(
            company=self.company, branch=self.branch, name="Credit Supplier",
        )
        SupplierBill.objects.create(
            company=self.company, branch=self.branch, supplier=supplier,
            invoice_number="SUP-001", invoice_date="2026-11-06",
            created_by=self.user,
        )
        deleted = self.client.delete(f"/api/v1/suppliers/{supplier.pk}")
        self.assertEqual(deleted.status_code, 409, getattr(deleted, "data", None))
        self.assertTrue(Supplier.objects.filter(pk=supplier.pk).exists())
