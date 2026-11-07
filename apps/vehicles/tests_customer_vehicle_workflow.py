"""Part 07: actual customer-vehicle persistence, tenant isolation, and history safety."""

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.customers.models import Customer, CustomerActivity
from apps.jobs.models import Job
from apps.roles.models import Role
from apps.vehicles.models import Vehicle


@override_settings(
    CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
    CREDIT_LABEL_ONLY_ACCESS_ENABLED=False,
)
class CustomerVehicleWorkflowTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Part Seven Workshop", slug="part-seven-workshop",
            require_paid_features=False,
        )
        self.other = Company.objects.create(
            name="Different Workshop", slug="part-seven-other",
            require_paid_features=False,
        )
        self.branch = Branch.objects.create(
            company=self.company, name="Service North", code="NORTH",
        )
        self.second = Branch.objects.create(
            company=self.company, name="Service South", code="SOUTH",
        )
        self.foreign_branch = Branch.objects.create(
            company=self.other, name="Foreign", code="FOREIGN",
        )
        role = Role.objects.create(
            company=self.company, name="Owner", code="PART07_OWNER",
            permissions=["*"], is_system=True,
        )
        self.owner = User.objects.create_user(
            email="part-seven-owner@example.test", password="StrongPass123!",
            name="Owner", company=self.company, branch=self.branch, role=role,
        )
        self.company.owner = self.owner
        self.company.save(update_fields=["owner"])
        self.customer = Customer.objects.create(
            company=self.company, branch=self.branch, name="First Customer",
            phone="+919888888888",
        )
        self.second_customer = Customer.objects.create(
            company=self.company, branch=self.second, name="Second Customer",
            phone="+919777777777",
        )
        self.foreign_customer = Customer.objects.create(
            company=self.other, branch=self.foreign_branch, name="Foreign",
            phone="+919666666666",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def make_vehicle(self, **changes):
        payload = {
            "customerId": str(self.customer.pk),
            "regNo": "KL 10 AB 1234", "make": "Honda", "model": "City",
        }
        payload.update(changes)
        return self.client.post("/api/v1/vehicles", payload, format="json")

    def test_add_vehicle_form_customer_id_persists_real_owner_branch(self):
        result = self.make_vehicle()
        self.assertEqual(result.status_code, 201, result.data)
        self.assertEqual(result.data["registration"], "KL10AB1234")
        self.assertEqual(str(result.data["customerId"]), str(self.customer.pk))
        self.assertEqual(str(result.data["branchId"]), str(self.branch.pk))
        self.assertEqual(result.data["customerStatus"], "active")
        vehicle = Vehicle.objects.get(pk=result.data["id"])
        self.assertEqual(vehicle.customer_id, self.customer.pk)
        self.assertEqual(vehicle.branch_id, self.branch.pk)

    def test_duplicate_registration_variations_are_rejected_and_preserved(self):
        first = self.make_vehicle()
        self.assertEqual(first.status_code, 201, first.data)
        duplicate = self.make_vehicle(
            customerId=str(self.second_customer.pk), regNo="kl-10-ab-1234",
        )
        self.assertEqual(duplicate.status_code, 400, duplicate.data)
        self.assertEqual(Vehicle.objects.filter(company=self.company).count(), 1)

    def test_missing_and_cross_tenant_customer_rejected_without_server_error(self):
        missing = self.client.post("/api/v1/vehicles", {
            "registration": "KL01AAAA1111", "make": "Suzuki",
        }, format="json")
        self.assertEqual(missing.status_code, 400, missing.data)
        cross = self.make_vehicle(customerId=str(self.foreign_customer.pk))
        self.assertEqual(cross.status_code, 400, cross.data)
        self.assertEqual(Vehicle.objects.filter(company=self.company).count(), 0)

    def test_nested_customer_vehicle_uses_customer_real_branch(self):
        endpoint = f"/api/v1/customers/{self.second_customer.pk}/vehicles"
        result = self.client.post(endpoint, {
            "regNo": "KL 14 CC 9876", "make": "Hyundai", "model": "i20",
        }, format="json")
        self.assertEqual(result.status_code, 201, result.data)
        vehicle = Vehicle.objects.get(pk=result.data["id"])
        self.assertEqual(vehicle.customer_id, self.second_customer.pk)
        self.assertEqual(vehicle.branch_id, self.second.pk)

    def test_vehicle_delete_archives_and_restores_without_erasing_history(self):
        result = self.make_vehicle()
        pk = result.data["id"]
        vehicle = Vehicle.objects.get(pk=pk)
        Job.objects.create(
            company=self.company, branch=self.branch,
            job_number="PART07-JOB001", customer=self.customer, vehicle=vehicle,
        )
        deleted = self.client.delete(f"/api/v1/vehicles/{pk}")
        self.assertEqual(deleted.status_code, 204)
        vehicle.refresh_from_db()
        self.assertEqual(vehicle.status, "Archived")
        self.assertEqual(vehicle.jobs.count(), 1)
        restored = self.client.post(f"/api/v1/vehicles/{pk}/restore", {}, format="json")
        self.assertEqual(restored.status_code, 200, restored.data)
        vehicle.refresh_from_db()
        self.assertEqual(vehicle.status, "Active")
        self.assertEqual(vehicle.jobs.count(), 1)
        self.assertEqual(CustomerActivity.objects.filter(
            company=self.company, activity_type="Vehicle Archived",
        ).count(), 1)

    def test_customer_archiving_keeps_vehicles_and_prevents_new_work(self):
        result = self.make_vehicle()
        pk = result.data["id"]
        archived = self.client.delete(f"/api/v1/customers/{self.customer.pk}")
        self.assertEqual(archived.status_code, 204)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.status, "archived")
        self.assertTrue(Vehicle.objects.filter(pk=pk).exists())
        new = self.make_vehicle(regNo="KL01DD2345")
        self.assertEqual(new.status_code, 400, new.data)
        self.client.delete(f"/api/v1/vehicles/{pk}")
        blocked_restore = self.client.post(f"/api/v1/vehicles/{pk}/restore", {}, format="json")
        self.assertEqual(blocked_restore.status_code, 400)
        restored = self.client.post(
            f"/api/v1/customers/{self.customer.pk}/restore", {}, format="json",
        )
        self.assertEqual(restored.status_code, 200, restored.data)
        self.assertEqual(restored.data["status"], "active")
        allowed_restore = self.client.post(f"/api/v1/vehicles/{pk}/restore", {}, format="json")
        self.assertEqual(allowed_restore.status_code, 200, allowed_restore.data)

    def test_vehicle_with_job_history_cannot_transfer_to_another_owner(self):
        created = self.make_vehicle()
        self.assertEqual(created.status_code, 201, created.data)
        pk = created.data["id"]
        vehicle = Vehicle.objects.get(pk=pk)
        Job.objects.create(
            company=self.company, branch=self.branch,
            job_number="PART07-JOB002", customer=self.customer, vehicle=vehicle,
        )
        result = self.client.patch(f"/api/v1/vehicles/{pk}", {
            "customerId": str(self.second_customer.pk),
        }, format="json")
        self.assertEqual(result.status_code, 400, result.data)
        vehicle.refresh_from_db()
        self.assertEqual(vehicle.customer_id, self.customer.pk)

    def test_new_owner_branch_moves_with_history_free_vehicle(self):
        created = self.make_vehicle()
        self.assertEqual(created.status_code, 201, created.data)
        pk = created.data["id"]
        moved = self.client.patch(f"/api/v1/vehicles/{pk}", {
            "customerId": str(self.second_customer.pk),
        }, format="json")
        self.assertEqual(moved.status_code, 200, moved.data)
        vehicle = Vehicle.objects.get(pk=pk)
        self.assertEqual(vehicle.customer_id, self.second_customer.pk)
        self.assertEqual(vehicle.branch_id, self.second.pk)

    def test_customer_duplicate_lookup_does_not_ignore_archived_filter(self):
        self.client.patch(f"/api/v1/customers/{self.customer.pk}/archive", {}, format="json")
        result = self.client.post(
            "/api/v1/customers/check-duplicate?status=active",
            {"phone": self.customer.phone}, format="json",
        )
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.data["duplicate"])

    def test_cross_tenant_vehicle_detail_not_disclosed(self):
        foreign = Vehicle.objects.create(
            company=self.other, branch=self.foreign_branch,
            customer=self.foreign_customer, registration="KL14XX4455",
        )
        self.assertEqual(self.client.get(f"/api/v1/vehicles/{foreign.pk}").status_code, 404)
        self.assertEqual(self.client.post(
            f"/api/v1/vehicles/{foreign.pk}/restore", {}, format="json",
        ).status_code, 404)
