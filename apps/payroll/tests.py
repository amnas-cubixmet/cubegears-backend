from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.branches.models import Branch
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.employees.models import Employee
from apps.jobs.models import Job
from apps.vehicles.models import Vehicle
from .models import EmployeeCompensationPlan, JobCardEmployeeAssignment, JobWorkSession
from .services import calculate_employee_payroll


class FixedWorkChargePayrollTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Workshop", slug="fixed-work-tests")
        self.branch = Branch.objects.create(company=self.company, name="Main", code="MAIN")
        self.customer = Customer.objects.create(
            company=self.company, branch=self.branch, name="Customer", phone="9000000000"
        )
        self.vehicle = Vehicle.objects.create(
            company=self.company, branch=self.branch, customer=self.customer,
            registration="KL10AA0001",
        )
        self.job = Job.objects.create(
            company=self.company, branch=self.branch, customer=self.customer,
            vehicle=self.vehicle, job_number="JOB-1001", status="In Progress",
            labour_total=Decimal("3500.00"),
        )
        self.worker = Employee.objects.create(
            company=self.company, branch=self.branch, name="Painter",
            employee_code="WRK-001", payment_type="per_job",
        )
        self.assignment = JobCardEmployeeAssignment.objects.create(
            company=self.company, branch=self.branch, job=self.job, employee=self.worker,
            commission_allocation_percent=Decimal("0"),
        )
        self.plan = EmployeeCompensationPlan.objects.create(
            company=self.company, branch=self.branch, employee=self.worker,
            payment_type="per_job", effective_from=timezone.localdate(),
            overtime_eligible=False, incentive_eligible=False,
        )

    def add_approved_work(self, customer_charge, worker_charge):
        now = timezone.now()
        return JobWorkSession.objects.create(
            company=self.company, branch=self.branch, job=self.job,
            employee=self.worker, assignment=self.assignment,
            service_name="Painting", labour_charge=Decimal(customer_charge),
            worker_charge=Decimal(worker_charge), status=JobWorkSession.APPROVED,
            started_at=now-timedelta(hours=2), completed_at=now,
            reviewed_at=now, elapsed_seconds=3600, approved_minutes=60,
        )

    def test_fixed_worker_charge_not_customer_labour_is_paid(self):
        first = self.add_approved_work("3500.00", "2000.00")
        second = self.add_approved_work("700.00", "500.00")
        today = timezone.localdate()
        result = calculate_employee_payroll(self.worker, self.plan, today.year, today.month)

        self.assertEqual(result["basic"], Decimal("2500.00"))
        self.assertEqual(result["net"], Decimal("2500.00"))
        self.assertEqual(result["commission_amount"], Decimal("0.00"))
        work_lines = [row for row in result["line_items"] if row["code"] == "WORK_CHARGE"]
        self.assertEqual(len(work_lines), 2)
        self.assertEqual({row["source_id"] for row in work_lines}, {str(first.pk), str(second.pk)})
        self.assertEqual(sum((row["amount"] for row in work_lines), Decimal("0")), Decimal("2500.00"))

    def test_unapproved_work_is_not_paid(self):
        session = self.add_approved_work("1500.00", "900.00")
        session.status = JobWorkSession.PENDING
        session.save(update_fields=["status"])
        today = timezone.localdate()
        result = calculate_employee_payroll(self.worker, self.plan, today.year, today.month)
        self.assertEqual(result["basic"], Decimal("0"))
        self.assertFalse(any(row["code"] == "WORK_CHARGE" for row in result["line_items"]))

    def test_previous_month_work_is_not_repaid(self):
        self.add_approved_work("2500.00", "1200.00")
        today = timezone.localdate()
        next_month = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
        result = calculate_employee_payroll(
            self.worker, self.plan, next_month.year, next_month.month
        )
        self.assertEqual(result["basic"], Decimal("0"))
