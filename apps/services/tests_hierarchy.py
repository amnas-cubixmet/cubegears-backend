"""Nested service categories preserve tenant isolation and historic catalogue rows."""
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.roles.models import Role
from apps.services.models import Service, ServiceCategory


@override_settings(CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
                   CREDIT_LABEL_ONLY_ACCESS_ENABLED=False)
class ServiceHierarchyTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Service Workshop", slug="service-workshop")
        self.branch = Branch.objects.create(company=self.company, name="Main", code="MAIN")
        role = Role.objects.create(company=self.company, name="Owner", code="HIERARCHY_OWNER",
                                   permissions=["*"], is_active=True)
        self.owner = User.objects.create_user(
            email="hierarchy-owner@example.test", password="test-password",
            name="Owner", company=self.company, branch=self.branch, role=role,
        )
        self.company.owner = self.owner
        self.company.save(update_fields=["owner"])
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)
        self.url = "/api/v1/services/categories"

    def add(self, name, parent=None):
        reply = self.client.post(
            self.url, {"name": name, "parentId": str(parent.pk) if parent else None},
            format="json",
        )
        self.assertEqual(reply.status_code, 201, reply.data)
        return ServiceCategory.objects.get(pk=reply.data["id"])

    def test_deep_nesting_and_move_without_rewriting_existing_services(self):
        root = self.add("Body Work")
        painting = self.add("Painting", root)
        door = self.add("Door Painting", painting)
        front = self.add("Front Door Painting", door)
        self.assertEqual(front.parent, door)
        service = Service.objects.create(
            company=self.company, branch=self.branch, category=front,
            name="Front Door Painting", code="PAINT-001", price="800.00",
        )
        move = self.client.patch(f"{self.url}/{painting.pk}", {
            "parentId": None,
        }, format="json")
        self.assertEqual(move.status_code, 200, move.data)
        service.refresh_from_db()
        self.assertEqual(service.category, front)
        self.assertEqual(painting.children.get(), door)
        rows = self.client.get(self.url)
        self.assertEqual(rows.status_code, 200)
        data = rows.data.get("results", []) if isinstance(rows.data, dict) else rows.data
        saved_front = next(row for row in data if str(row["id"]) == str(front.pk))
        self.assertEqual(str(saved_front["parentId"]), str(door.pk))

    def test_no_cycle_self_parent_or_foreign_workshop_parent(self):
        root = self.add("Mechanical")
        child = self.add("Engine", root)
        self.assertEqual(self.client.patch(
            f"{self.url}/{root.pk}", {"parentId": str(child.pk)}, format="json",
        ).status_code, 400)
        self.assertEqual(self.client.patch(
            f"{self.url}/{root.pk}", {"parentId": str(root.pk)}, format="json",
        ).status_code, 400)
        foreign = Company.objects.create(name="Outside", slug="foreign-categories")
        other = ServiceCategory.objects.create(company=foreign, name="Secret")
        self.assertEqual(self.client.patch(
            f"{self.url}/{root.pk}", {"parentId": str(other.pk)}, format="json",
        ).status_code, 400)
        root.refresh_from_db()
        self.assertIsNone(root.parent_id)

    def test_populated_categories_cannot_be_deleted_and_can_be_deactivated(self):
        root = self.add("Electrical")
        child = self.add("AC", root)
        self.assertEqual(self.client.delete(f"{self.url}/{root.pk}").status_code, 400)
        reply = self.client.patch(f"{self.url}/{root.pk}", {
            "is_active": False,
        }, format="json")
        self.assertEqual(reply.status_code, 200)
        root.refresh_from_db()
        self.assertFalse(root.is_active)
        self.assertEqual(child.parent, root)

    def test_service_cat_unit_tax_and_checklist_persist_without_duplicate_catalogue(self):
        category = self.add("Detailing")
        payload = {
            "name": "Interior Detailing", "code": "DET-01",
            "categoryId": str(category.pk), "price": "850.00",
            "pricingType": "Fixed", "unit": "Job", "hsnSac": "998714",
            "taxRate": "18", "taxTreatment": "taxable",
            "labour_hours": "1.50", "checklist": ["Interior cleaning"],
        }
        reply = self.client.post("/api/v1/services", payload, format="json")
        self.assertEqual(reply.status_code, 201, reply.data)
        self.assertEqual(reply.data["categoryName"], category.name)
        self.assertEqual(reply.data["durationMinutes"], 90)
        self.assertEqual(reply.data["taxRate"], "18.00")
        self.assertEqual(reply.data["hsnSac"], "998714")
        self.assertEqual(Service.objects.filter(company=self.company, code="DET-01").count(), 1)
