from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.employees.models import Employee
from apps.expenses.models import Expense
from apps.roles.models import Role
from apps.vehicles.models import Vehicle
from apps.payroll.models import EmployeeCompensationPlan
from .models import Job, OutsideLabourCharge


class ManualOutsideLabourTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Garage", slug="outside-labour-test")
        self.branch = Branch.objects.create(company=self.company, name="Main", code="MAIN")
        self.role = Role.objects.create(
            company=self.company, name="Manager", code="MANAGER",
            permissions=["jobs.view", "expenses.view", "expenses.create", "expenses.edit", "expenses.delete"],
        )
        self.user = User.objects.create(
            company=self.company, branch=self.branch, role=self.role,
            name="Owner", email="owner@outside-labour.test",
        )
        customer = Customer.objects.create(
            company=self.company, branch=self.branch,
            name="Customer", phone="9000000000",
        )
        vehicle = Vehicle.objects.create(
            company=self.company, branch=self.branch,
            customer=customer, registration="KL10AA5555",
        )
        self.job = Job.objects.create(
            company=self.company, branch=self.branch, customer=customer,
            vehicle=vehicle, job_number="JOB-123", status="In Progress",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.endpoint = "/api/v1/outside-labour"

    def create_outside_labour(self):
        return self.client.post(self.endpoint, {
            "job": str(self.job.pk),
            "workerName": "External Painter",
            "workDescription": "Bumper painting",
            "customerCharge": "3500.00",
            "workerCharge": "2000.00",
        }, format="json")

    def test_manual_worker_never_creates_staff_or_payroll_and_one_expense_only(self):
        created = self.create_outside_labour()
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(created.data["status"], "Pending")
        self.assertEqual(Expense.objects.count(), 0)
        self.assertEqual(Employee.objects.count(), 0)
        self.assertEqual(EmployeeCompensationPlan.objects.count(), 0)

        record_id = created.data["id"]
        paid = self.client.post(f"{self.endpoint}/{record_id}/pay", {
            "paymentMethod": "UPI", "paymentReference": "TXN-123",
        }, format="json")
        self.assertEqual(paid.status_code, 200, paid.data)
        self.assertEqual(paid.data["status"], "Paid")
        self.assertEqual(Expense.objects.count(), 1)
        expense = Expense.objects.get()
        self.assertEqual(expense.category, "Outside Labour")
        self.assertEqual(expense.amount, Decimal("2000.00"))
        self.assertEqual(expense.vendor, "External Painter")

        repeated = self.client.post(f"{self.endpoint}/{record_id}/pay", {
            "paymentMethod": "UPI", "paymentReference": "TXN-123",
        }, format="json")
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(Expense.objects.count(), 1)
        self.assertEqual(Employee.objects.count(), 0)

    def test_paid_record_cannot_be_edited_or_deleted(self):
        created = self.create_outside_labour()
        self.assertEqual(created.status_code, 201, created.data)
        record_id = created.data["id"]
        self.client.post(f"{self.endpoint}/{record_id}/pay", {"paymentMethod": "Cash"}, format="json")
        changed = self.client.patch(
            f"{self.endpoint}/{record_id}",
            {"workerCharge": "500.00"}, format="json",
        )
        self.assertEqual(changed.status_code, 400, changed.data)
        removed = self.client.delete(f"{self.endpoint}/{record_id}")
        self.assertEqual(removed.status_code, 400)
        self.assertEqual(OutsideLabourCharge.objects.count(), 1)
        self.assertEqual(Expense.objects.count(), 1)

    def test_cross_company_job_rejected(self):
        other_company = Company.objects.create(name="Other", slug="other-labour-test")
        other_branch = Branch.objects.create(company=other_company, name="Other", code="OTHER")
        customer = Customer.objects.create(
            company=other_company, branch=other_branch, name="Other Person", phone="9000000002",
        )
        vehicle = Vehicle.objects.create(
            company=other_company, branch=other_branch, customer=customer,
            registration="KL10AA5556",
        )
        job = Job.objects.create(
            company=other_company, branch=other_branch, customer=customer,
            vehicle=vehicle, job_number="JOB-OTHER",
        )
        invalid = self.client.post(self.endpoint, {
            "job": str(job.pk), "workerName": "External",
            "workDescription": "Paint", "workerCharge": "1500.00",
        }, format="json")
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(OutsideLabourCharge.objects.count(), 0)
