"""Tenant isolation, permissions and persistence of make/model options."""

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.roles.models import Role
from apps.vehicles.models import Vehicle, VehicleCatalogEntry

URL = "/api/v1/vehicle-catalog"


@override_settings(CREDIT_FEATURE_ENFORCEMENT_ENABLED=False, CREDIT_LABEL_ONLY_ACCESS_ENABLED=False)
class VehicleCatalogTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Catalog Workshop", slug="catalog-test-workshop")
        owner_role = Role.objects.create(
            company=self.company, name="Owner", code="CATALOG_OWNER",
            permissions=["*"], is_active=True,
        )
        self.owner = User.objects.create_user(
            email="catalog-owner@example.test", password="not-used",
            name="Catalog Owner", company=self.company, role=owner_role,
        )
        self.company.owner = self.owner
        self.company.save(update_fields=["owner"])
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)

    def test_preloaded_makes_and_models_are_available(self):
        response = self.client.get(URL)
        self.assertEqual(response.status_code, 200, response.data)
        makes = {item["make"]: item["models"] for item in response.data["makes"]}
        self.assertIn("Toyota", makes)
        self.assertIn("Innova Crysta", makes["Toyota"])
        self.assertIn("Maruti Suzuki", makes)
        self.assertIn("Swift", makes["Maruti Suzuki"])
        self.assertEqual(response["Cache-Control"], "private, no-store")

    def test_add_make_and_multiple_models_survive_fresh_reads(self):
        first = self.client.post(URL, {"make": "My New Make"}, format="json")
        self.assertEqual(first.status_code, 201, first.data)
        first_model = self.client.post(
            URL, {"make": "My New Make", "model": "Model Alpha"}, format="json"
        )
        second_model = self.client.post(
            URL, {"make": "My New Make", "model": "Model Beta"}, format="json"
        )
        self.assertEqual(first_model.status_code, 201, first_model.data)
        self.assertEqual(second_model.status_code, 201, second_model.data)

        again = self.client.post(
            URL, {"make": "  my   new make  ", "model": " MODEL ALPHA "}, format="json"
        )
        self.assertEqual(again.status_code, 200, again.data)
        self.assertFalse(again.data["created"])
        self.assertEqual(VehicleCatalogEntry.objects.filter(company=self.company).count(), 3)

        makes = {row["make"]: row["models"] for row in self.client.get(URL).data["makes"]}
        self.assertIn("My New Make", makes)
        self.assertEqual(makes["My New Make"], ["Model Alpha", "Model Beta"])

    def test_existing_vehicle_models_appear_without_manual_catalog_entry(self):
        customer = Customer.objects.create(
            company=self.company, name="Vehicle Owner", phone="+919123456780",
        )
        Vehicle.objects.create(
            company=self.company, customer=customer,
            registration="KL10AB1234", make="Custom Vehicles", model="Series Q",
        )
        makes = {row["make"]: row["models"] for row in self.client.get(URL).data["makes"]}
        self.assertEqual(makes["Custom Vehicles"], ["Series Q"])

    def test_invalid_or_malicious_input_is_rejected(self):
        for payload in [
            {"make": ""},
            {"make": " "},
            {"make": "A" * 81},
            {"make": "A\nB"},
            {"make": 19},
            {"make": "Good", "model": "B" * 81},
            {"make": "Good", "model": "B\rC"},
            {"make": "Good", "model": None},
            {"make": "Good", "model": "", "admin": True},
        ]:
            result = self.client.post(URL, payload, format="json")
            self.assertEqual(result.status_code, 400, (payload, result.data))
        self.assertFalse(VehicleCatalogEntry.objects.exists())

    def test_catalog_is_isolated_between_companies(self):
        self.client.post(URL, {"make": "Private Brand", "model": "Secret Model"}, format="json")
        other = Company.objects.create(name="Other Catalog Workshop", slug="other-catalog-test")
        role = Role.objects.create(company=other, name="Owner", code="OTHER_OWNER",
                                   permissions=["*"], is_active=True)
        owner = User.objects.create_user(
            email="other-catalog@example.test", password="not-used",
            name="Other Owner", company=other, role=role,
        )
        other.owner = owner
        other.save(update_fields=["owner"])
        client = APIClient()
        client.force_authenticate(user=owner)
        result = client.get(URL)
        self.assertEqual(result.status_code, 200, result.data)
        self.assertNotIn("Private Brand", [r["make"] for r in result.data["makes"]])
        self.assertEqual(VehicleCatalogEntry.objects.filter(company=other).count(), 0)

    def test_job_create_role_can_add_models_but_view_only_cannot(self):
        role = Role.objects.create(
            company=self.company, name="Job intake", code="JOB_INTAKE",
            permissions=["jobs.create"], is_active=True,
        )
        user = User.objects.create_user(
            email="catalog-intake@example.test", password="not-used",
            name="Intake", company=self.company, role=role,
        )
        client = APIClient()
        client.force_authenticate(user=user)
        # Read and write both work with jobs.create, even without vehicles.view.
        self.assertEqual(client.get(URL).status_code, 200)
        created = client.post(URL, {"make": "Intake Brand", "model": "One"}, format="json")
        self.assertEqual(created.status_code, 201, created.data)

        role.permissions = ["jobs.view"]
        role.save(update_fields=["permissions"])
        self.assertEqual(client.get(URL).status_code, 200)
        self.assertEqual(client.post(URL, {"make": "Forbidden"}, format="json").status_code, 403)

        self.assertIn(APIClient().get(URL).status_code, (401, 403))
