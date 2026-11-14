"""Stable, company-scoped supplier codes must not leak UUIDs into the UI."""
from django.test import TestCase, override_settings

from apps.accounts.models import User
from apps.companies.models import Company
from apps.inventory.models import Supplier, SupplierCodeSequence
from apps.roles.models import Role
from apps.suppliers.serializers import SupplierSerializer
from rest_framework.test import APIClient


@override_settings(
    CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
    CREDIT_LABEL_ONLY_ACCESS_ENABLED=False,
)
class SupplierReferenceTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Code Workshop A", slug="supplier-codes-a")
        self.other = Company.objects.create(name="Code Workshop B", slug="supplier-codes-b")

    def test_created_codes_are_monotonic_per_company(self):
        first = Supplier.objects.create(company=self.company, name="First Supplier")
        second = Supplier.objects.create(company=self.company, name="Second Supplier")
        other = Supplier.objects.create(company=self.other, name="Other Workshop Supplier")
        self.assertEqual(first.code, "SUP-00001")
        self.assertEqual(second.code, "SUP-00002")
        self.assertEqual(other.code, "SUP-00001")
        self.assertEqual(SupplierCodeSequence.objects.get(company=self.company).next_number, 3)

    def test_code_stays_stable_after_rename_and_delete(self):
        first = Supplier.objects.create(company=self.company, name="First")
        second = Supplier.objects.create(company=self.company, name="Second")
        second.name = "Updated Second"
        second.save(update_fields=["name"])
        second.refresh_from_db()
        self.assertEqual(second.code, "SUP-00002")
        first.delete()
        third = Supplier.objects.create(company=self.company, name="Third")
        self.assertEqual(third.code, "SUP-00003")

    def test_serialized_code_is_read_only_and_uuid_remains_api_identifier(self):
        supplier = Supplier.objects.create(company=self.company, name="Parts Distributor")
        serialized = SupplierSerializer(instance=supplier)
        self.assertEqual(serialized.data["code"], "SUP-00001")
        self.assertEqual(str(serialized.data["id"]), str(supplier.id))
        update = SupplierSerializer(instance=supplier, data={"code": "SUP-99999"}, partial=True)
        self.assertTrue(update.is_valid(), update.errors)
        update.save()
        supplier.refresh_from_db()
        self.assertEqual(supplier.code, "SUP-00001")

    def test_supplier_detail_api_returns_reference_code(self):
        role = Role.objects.create(
            company=self.company, name="Owner", code="SUPPLIER_CODE_OWNER",
            permissions=["*"], is_active=True, is_system=True,
        )
        owner = User.objects.create_user(
            email="supplier-code-owner@example.test",
            password="some-long-demo-password", name="Owner", company=self.company, role=role,
        )
        self.company.owner = owner
        self.company.save(update_fields=["owner"])
        client = APIClient()
        client.force_authenticate(user=owner)
        supplier = Supplier.objects.create(company=self.company, name="Test Supplier")
        detail = client.get(f"/api/v1/suppliers/{supplier.id}")
        self.assertEqual(detail.status_code, 200, getattr(detail, "data", None))
        self.assertEqual(detail.data["code"], "SUP-00001")
