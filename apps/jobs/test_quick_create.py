from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.roles.models import Role
from apps.vehicles.models import Vehicle
from .models import Job


class QuickCreateJobTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Quick Workshop", slug="quick-jobs-test")
        self.branch = Branch.objects.create(company=self.company, name="Main", code="MAIN")
        self.role = Role.objects.create(
            company=self.company, name="Manager", code="MANAGER",
            permissions=["jobs.view", "jobs.create", "jobs.edit"],
        )
        self.user = User.objects.create(
            company=self.company, branch=self.branch, role=self.role,
            name="Manager", email="manager@quickjobs.test",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.endpoint = "/api/v1/jobs"

    def payload(self):
        return {
            "customerName": "Ali", "customerPhone": "9876543210",
            "vehicleReg": "KL 10 AB 1001", "vehicleInfo": "Maruti Swift",
            "fuelLevel": "50%", "kilometre": "42500", "status": "New",
            "complaints": [{"id": "CMP-1", "description": "Brake noise", "status": "Open"}],
        }

    def test_quick_create_resolves_customer_and_vehicle(self):
        created = self.client.post(self.endpoint, self.payload(), format="json")
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(Customer.objects.filter(company=self.company).count(), 1)
        self.assertEqual(Vehicle.objects.filter(company=self.company).count(), 1)
        job = Job.objects.get(pk=created.data["id"])
        self.assertEqual(job.odometer, 42500)
        self.assertEqual(job.fuel_level, "50%")
        self.assertEqual(job.customer.phone, "9876543210")
        self.assertEqual(created.data["customerPhone"], "9876543210")
        self.assertEqual(created.data["vehicleReg"], "KL 10 AB 1001")

    def test_job_overview_returns_km_fuel_and_vehicle_vin(self):
        payload = {
            **self.payload(),
            "vin": "MA3EUA61S00123456",
            "fuelLevel": "75%",
            "kilometre": "150",
        }
        created = self.client.post(self.endpoint, payload, format="json")
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(created.data["vin"], payload["vin"])
        self.assertEqual(created.data["fuelLevel"], "75%")
        self.assertEqual(created.data["kilometre"], 150)

        retrieved = self.client.get(f"{self.endpoint}/{created.data['id']}")
        self.assertEqual(retrieved.status_code, 200, retrieved.data)
        self.assertEqual(retrieved.data["vin"], payload["vin"])
        self.assertEqual(retrieved.data["fuelLevel"], "75%")
        self.assertEqual(retrieved.data["kilometre"], 150)

    def test_repeat_visit_reuses_vehicle_and_customer(self):
        first = self.client.post(self.endpoint, self.payload(), format="json")
        self.assertEqual(first.status_code, 201, first.data)
        payload = {**self.payload(), "vehicleReg": "KL10AB1001"}
        second = self.client.post(self.endpoint, payload, format="json")
        self.assertEqual(second.status_code, 201, second.data)
        self.assertEqual(Customer.objects.count(), 1)
        self.assertEqual(Vehicle.objects.count(), 1)
        self.assertEqual(Job.objects.count(), 2)

    def test_mismatching_vehicle_owner_is_rejected(self):
        self.client.post(self.endpoint, self.payload(), format="json")
        payload = {**self.payload(), "customerPhone": "9000000001"}
        result = self.client.post(self.endpoint, payload, format="json")
        self.assertEqual(result.status_code, 400, result.data)
        self.assertIn("vehicleReg", result.data)
        self.assertEqual(Job.objects.count(), 1)
        self.assertEqual(Customer.objects.count(), 1)

    def test_partial_inspection_patch_does_not_require_customer_again(self):
        created = self.client.post(self.endpoint, self.payload(), format="json")
        self.assertEqual(created.status_code, 201, created.data)
        response = self.client.patch(
            f"{self.endpoint}/{created.data['id']}",
            {"inspection": {"status": "In Progress", "checklist": {"Tyres": "Good"}}},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["inspection"]["checklist"]["Tyres"], "Good")

    def test_existing_customer_selection_creates_vehicle_for_that_customer(self):
        selected = Customer.objects.create(
            company=self.company, branch=self.branch,
            name="Selected Customer", phone="9001234567",
            email="selected@test.local", address="Sample Street",
        )
        payload = {
            **self.payload(),
            "customer": str(selected.id),
            "customerName": selected.name,
            "customerPhone": selected.phone,
            "customerEmail": selected.email,
            "customerAddress": selected.address,
            "vehicleReg": "KL 10 AB 4567",
        }
        created = self.client.post(self.endpoint, payload, format="json")
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(Customer.objects.filter(company=self.company).count(), 1)
        job = Job.objects.get(pk=created.data["id"])
        self.assertEqual(job.customer_id, selected.id)
        self.assertEqual(job.vehicle.customer_id, selected.id)
        self.assertEqual(job.vehicle.registration, "KL 10 AB 4567")

    def test_selected_customer_reuses_owned_vehicle(self):
        selected = Customer.objects.create(
            company=self.company, branch=self.branch,
            name="Owned Customer", phone="9876543210",
        )
        vehicle = Vehicle.objects.create(
            company=self.company, branch=self.branch, customer=selected,
            registration="KL 10 AB 1001", make="Maruti", model="Swift",
        )
        payload = {
            **self.payload(),
            "customer": str(selected.id),
            "vehicle": str(vehicle.id),
            "customerName": selected.name,
            "customerPhone": selected.phone,
        }
        created = self.client.post(self.endpoint, payload, format="json")
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(Vehicle.objects.filter(company=self.company).count(), 1)
        self.assertEqual(Job.objects.get(pk=created.data["id"]).vehicle_id, vehicle.id)

    def test_selected_customer_cannot_use_vehicle_owned_by_another_customer(self):
        owner = Customer.objects.create(
            company=self.company, branch=self.branch,
            name="Original Owner", phone="9876543210",
        )
        selected = Customer.objects.create(
            company=self.company, branch=self.branch,
            name="Different Owner", phone="9001234567",
        )
        vehicle = Vehicle.objects.create(
            company=self.company, branch=self.branch, customer=owner,
            registration="KL 10 AB 1001",
        )
        created = self.client.post(self.endpoint, {
            **self.payload(),
            "customer": str(selected.id),
            "customerName": selected.name,
            "customerPhone": selected.phone,
            "vehicle": str(vehicle.id),
        }, format="json")
        self.assertEqual(created.status_code, 400, created.data)
        self.assertIn("vehicleReg", created.data)
        self.assertEqual(Job.objects.count(), 0)

    def test_selected_customer_name_must_match_saved_record(self):
        customer = Customer.objects.create(
            company=self.company, branch=self.branch,
            name="Saved Customer", phone="9876543210",
        )
        created = self.client.post(self.endpoint, {
            **self.payload(),
            "customer": str(customer.id),
            "customerName": "Other Customer",
        }, format="json")
        self.assertEqual(created.status_code, 400, created.data)
        self.assertIn("customer", created.data)
        self.assertEqual(Vehicle.objects.count(), 0)
