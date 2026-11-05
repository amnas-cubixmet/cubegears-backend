"""Job create API regression for vehicle metadata, staff assignment and complaints."""
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.employees.models import Employee
from apps.jobs.models import Job
from apps.roles.models import Role
from apps.vehicles.models import Vehicle


@override_settings(
    CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
    CREDIT_LABEL_ONLY_ACCESS_ENABLED=False,
)
class JobCreateVehicleComplaintTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Job UI Workshop", slug="job-ui-workshop")
        self.branch = Branch.objects.create(
            company=self.company, name="Main Garage", code="MAIN", is_active=True,
        )
        self.role = Role.objects.create(
            company=self.company, name="Owner", code="JOB_OWNER",
            permissions=["*"], is_active=True, is_system=True,
        )
        self.owner = User.objects.create_user(
            email="job-ui-owner@example.test", password="safe-password",
            phone="+919876543210", name="Workshop Owner", company=self.company,
            branch=self.branch, role=self.role,
        )
        self.company.owner = self.owner
        self.company.save(update_fields=["owner"])
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)
        self.customer = Customer.objects.create(
            company=self.company, branch=self.branch,
            name="Vehicle Customer", phone="9876543210",
        )

    def payload(self, reg="KL10AB1111"):
        return {
            "customer":str(self.customer.id),
            "customerName":self.customer.name,
            "customerPhone":"+919876543210",
            "vehicleReg":reg,
            "vehicleMake":"Maruti Suzuki",
            "vehicleModel":"Swift VXI",
            "fuelType":"Petrol",
            "kilometre":"52500",
            "fuelLevel":"50%",
            "branchId":str(self.branch.pk),
            "status":"New",
            "priority":"Medium",
            "complaints":[
                {"id":"CMP-A","description":"AC Not Cooling","wording":"AC Not Cooling","status":"Open"},
                {"id":"CMP-B","description":"Brake Noise","wording":"Brake Noise","status":"Open"},
                {"id":"CMP-C","description":"Engine Vibration","wording":"Engine Vibration","status":"Open"},
            ],
        }

    def test_multiple_complaints_and_separate_vehicle_fields_are_saved(self):
        resp = self.client.post("/api/v1/jobs", self.payload(), format="json")
        self.assertEqual(resp.status_code, 201, getattr(resp, "data", None))
        job = Job.objects.select_related("vehicle").get(pk=resp.data["id"])
        self.assertEqual(job.vehicle.make, "Maruti Suzuki")
        self.assertEqual(job.vehicle.model, "Swift VXI")
        self.assertEqual(job.vehicle.fuel_type, "Petrol")
        self.assertEqual(job.vehicle.odometer, 52500)
        self.assertEqual(job.odometer, 52500)
        self.assertEqual(job.fuel_level, "50%")
        self.assertEqual(job.branch_id, self.branch.pk)
        self.assertEqual([p["description"] for p in job.complaints],
                         ["AC Not Cooling", "Brake Noise", "Engine Vibration"])
        self.assertEqual([p["id"] for p in job.complaints], ["CMP-A","CMP-B","CMP-C"])

    def test_saved_vehicle_stays_untouched_without_explicit_update(self):
        vehicle = Vehicle.objects.create(
            company=self.company, branch=self.branch, customer=self.customer,
            registration="KL10AB2222", make="Toyota", model="Etios", fuel_type="Diesel",
            odometer=45000,
        )
        request = self.payload(reg="KL10AB2222")
        request["vehicle"] = str(vehicle.pk)
        resp = self.client.post("/api/v1/jobs", request, format="json")
        self.assertEqual(resp.status_code, 201, getattr(resp, "data", None))
        vehicle.refresh_from_db()
        self.assertEqual(vehicle.make, "Toyota")
        self.assertEqual(vehicle.odometer, 45000)
        job = Job.objects.get(pk=resp.data["id"])
        self.assertEqual(job.odometer, 52500)

    def test_authorized_explicit_vehicle_update_changes_only_metadata(self):
        vehicle = Vehicle.objects.create(
            company=self.company, branch=self.branch, customer=self.customer,
            registration="KL10AB3333", make="Toyota", model="Etios", fuel_type="Diesel",
        )
        request = self.payload(reg="KL10AB3333")
        request["vehicle"] = str(vehicle.pk)
        request["vehicleDetailsUpdate"] = True
        resp = self.client.post("/api/v1/jobs", request, format="json")
        self.assertEqual(resp.status_code, 201, getattr(resp, "data", None))
        vehicle.refresh_from_db()
        self.assertEqual(vehicle.make, "Maruti Suzuki")
        self.assertEqual(vehicle.model, "Swift VXI")
        self.assertEqual(vehicle.fuel_type, "Petrol")
        self.assertEqual(vehicle.odometer, 52500)

    def test_service_advisor_and_technician_are_resolved_from_same_company(self):
        user = User.objects.create_user(
            email="mechanic@example.test", password="safe-pass", name="Mechanic",
            phone="+919888887777",company=self.company,branch=self.branch,
            role=self.role,
        )
        employee = Employee.objects.create(
            company=self.company, branch=self.branch, user=user,
            name="Mechanic", employee_code="EMP-008", status="Active",
        )
        payload = self.payload(reg="KL10AB4444")
        payload["assignedEmployeeId"] = str(employee.pk)
        payload["serviceAdvisorEmployeeId"] = str(employee.pk)
        resp = self.client.post("/api/v1/jobs", payload, format="json")
        self.assertEqual(resp.status_code, 201, getattr(resp,"data",None))
        job = Job.objects.get(pk=resp.data["id"])
        self.assertEqual(job.technician_id, user.pk)
        self.assertEqual(job.advisor_id, user.pk)

    def test_wrong_workshop_branch_rejected_without_create(self):
        stranger = Company.objects.create(name="Other Workshop", slug="other-job-ui-workshop")
        foreign_branch = Branch.objects.create(
            company=stranger, name="Other Branch", code="MAIN", is_active=True,
        )
        payload = self.payload(reg="KL10AB5555")
        payload["branchId"] = str(foreign_branch.pk)
        resp = self.client.post("/api/v1/jobs", payload, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Job.objects.filter(company=self.company).count(), 0)
        self.assertEqual(Vehicle.objects.filter(company=self.company).count(), 0)

    def test_legacy_combined_make_model_still_supported(self):
        payload = self.payload(reg="KL10AB6666")
        payload.pop("vehicleMake")
        payload.pop("vehicleModel")
        payload["vehicleInfo"] = "Toyota Innova"
        resp = self.client.post("/api/v1/jobs", payload, format="json")
        self.assertEqual(resp.status_code,201,getattr(resp,"data",None))
        vehicle = Vehicle.objects.get(company=self.company,registration="KL10AB6666")
        self.assertEqual(vehicle.make,"Toyota")
        self.assertEqual(vehicle.model,"Innova")
