"""Inventory part identifiers and company-persisted rack suggestions."""
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.inventory.models import StockItem
from apps.roles.models import Role


@override_settings(CREDIT_FEATURE_ENFORCEMENT_ENABLED=False, CREDIT_LABEL_ONLY_ACCESS_ENABLED=False)
class StockIdentifiersTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Parts Garage", slug="parts-garage-tests")
        self.branch = Branch.objects.create(company=self.company, name="Main", code="M", is_active=True)
        role = Role.objects.create(
            company=self.company, name="Owner", code="STOCK_PART_OWNER",
            permissions=["*"], is_active=True, is_system=True,
        )
        user = User.objects.create_user(
            email="parts-owner@example.test", password="safe-password",
            name="Parts Owner", phone="+919999000003",
            company=self.company, branch=self.branch, role=role,
        )
        self.company.owner = user
        self.company.save(update_fields=["owner"])
        self.client = APIClient()
        self.client.force_authenticate(user=user)

    def create_part(self, **fields):
        payload = {
            "partName": "Brake Pad", "partNumber": "BP-2045",
            "costPrice": "500.00", "sellingPrice": "750.00",
            "location": "Left Top", "hsnCode": "", "tax": "0",
            **fields,
        }
        return self.client.post("/api/v1/stock/items", payload, format="json")

    def test_part_number_and_auto_sku_are_separate_and_existing_skus_survive(self):
        existing = StockItem.objects.create(
            company=self.company, branch=self.branch,
            name="Old oil", sku="OIL-OLD-001", rack="Left Bottom",
        )
        first = self.create_part()
        self.assertEqual(first.status_code, 201, getattr(first, "data", None))
        self.assertEqual(first.data["partNumber"], "BP-2045")
        self.assertEqual(first.data["sku"], "CG-PRT-000001")
        next_part = self.create_part(partName="Oil filter", partNumber="OF/901")
        self.assertEqual(next_part.status_code, 201, getattr(next_part, "data", None))
        self.assertEqual(next_part.data["sku"], "CG-PRT-000002")
        existing.refresh_from_db()
        self.assertEqual(existing.sku, "OIL-OLD-001")
        self.assertEqual(existing.part_number, "")

    def test_manual_sku_uniqueness_and_manufacturer_part_format(self):
        self.assertEqual(self.create_part(sku="BP-WORK-1").status_code, 201)
        duplicate = self.create_part(partName="Another", sku="bp-work-1")
        self.assertEqual(duplicate.status_code, 400)
        self.assertIn("sku", duplicate.data)
        bad_part = self.create_part(partNumber="INVALID@CODE", sku="UNIQUE-02")
        self.assertEqual(bad_part.status_code, 400)
        self.assertIn("partNumber", bad_part.data)

    def test_saved_rack_location_is_available_to_authorized_sessions(self):
        self.assertEqual(self.create_part(location="Left Top").status_code, 201)
        result = self.client.get("/api/v1/stock/items/rack-locations", {"q": "lef"})
        self.assertEqual(result.status_code, 200, getattr(result, "data", None))
        self.assertIn("Left Top", result.data)
        self.assertNotIn("Right Shelf", result.data)

    def test_search_part_number_supplier_category_and_rack(self):
        created = self.create_part(location="Right Shelf A")
        self.assertEqual(created.status_code, 201, getattr(created, "data", None))
        for q in ("BP-2045", "Right Shelf"):
            results = self.client.get("/api/v1/stock/items", {"search": q})
            self.assertEqual(results.status_code, 200, getattr(results, "data", None))
            items = results.data if isinstance(results.data, list) else results.data["results"]
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["id"], created.data["id"])

    def test_zero_gst_classification_is_persisted_without_changing_rate(self):
        for index, classification in enumerate(("exempt", "nil_rated", "zero_rated", "non_gst")):
            response = self.create_part(
                partName=f"0% Category Part {index}",
                tax="0",
                zeroTaxClassification=classification,
            )
            self.assertEqual(response.status_code, 201, getattr(response, "data", None))
            self.assertEqual(response.data["zeroTaxClassification"], classification)
            saved = StockItem.objects.get(pk=response.data["id"])
            self.assertEqual(saved.gst_zero_classification, classification)
            self.assertEqual(saved.tax, 0)
            detail = self.client.get(f"/api/v1/stock/items/{saved.pk}")
            self.assertEqual(detail.status_code, 200)
            self.assertEqual(detail.data["zeroTaxClassification"], classification)

    def test_invalid_zero_classification_or_nonzero_rate_mismatch_is_rejected(self):
        invalid = self.create_part(
            partName="Invalid category", zeroTaxClassification="other"
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertIn("zeroTaxClassification", invalid.data)

        mismatch = self.create_part(
            partName="Wrong tax mode", tax="5", zeroTaxClassification="exempt"
        )
        self.assertEqual(mismatch.status_code, 400)
        self.assertIn("zeroTaxClassification", mismatch.data)

    def test_changing_a_classified_zero_rate_to_five_clears_stale_category(self):
        created = self.create_part(
            partName="Changeable classification", tax="0", zeroTaxClassification="exempt"
        )
        self.assertEqual(created.status_code, 201, getattr(created, "data", None))
        updated = self.client.patch(
            f"/api/v1/stock/items/{created.data['id']}",
            {"tax": "5"}, format="json",
        )
        self.assertEqual(updated.status_code, 200, getattr(updated, "data", None))
        self.assertEqual(updated.data["zeroTaxClassification"], "")
        item = StockItem.objects.get(pk=created.data["id"])
        self.assertEqual(item.tax, 5)
        self.assertEqual(item.gst_zero_classification, "")

    def test_legacy_unclassified_zero_rate_remains_supported(self):
        response = self.create_part(partName="Legacy part")
        self.assertEqual(response.status_code, 201, getattr(response, "data", None))
        self.assertEqual(response.data["zeroTaxClassification"], "")
