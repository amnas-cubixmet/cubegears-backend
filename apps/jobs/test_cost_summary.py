from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.employees.models import Employee
from apps.inventory.models import StockItem, StockMovement
from apps.payroll.models import JobCardEmployeeAssignment, JobWorkSession
from apps.roles.models import Role
from apps.vehicles.models import Vehicle
from .models import Job, OutsideLabourCharge


class JobCostSummaryTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Cost Workshop", slug="job-cost-test")
        self.branch = Branch.objects.create(company=self.company, name="Main", code="MAIN")
        self.role = Role.objects.create(
            company=self.company, name="Owner", code="OWNER",
            permissions=["jobs.view", "payroll.view", "expenses.view", "stock.view"],
        )
        self.user = User.objects.create(
            company=self.company, branch=self.branch, role=self.role,
            name="Owner", email="owner@cost.local",
        )
        self.customer = Customer.objects.create(
            company=self.company, branch=self.branch, name="Customer", phone="9900112233",
        )
        self.vehicle = Vehicle.objects.create(
            company=self.company, branch=self.branch, customer=self.customer,
            registration="KL10AB1234",
        )
        self.job = Job.objects.create(
            company=self.company, branch=self.branch, customer=self.customer,
            vehicle=self.vehicle, job_number="COST-01", status="In Progress",
            labour_total=Decimal("9000.00"),
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.url = f"/api/v1/jobs/{self.job.id}/cost-summary"

    def test_only_records_direct_cost_not_customer_revenue(self):
        employee = Employee.objects.create(
            company=self.company, branch=self.branch,
            name="Mechanic", employee_code="MECH-1", payment_type="per_job",
        )
        assignment = JobCardEmployeeAssignment.objects.create(
            company=self.company, branch=self.branch, job=self.job, employee=employee,
        )
        JobWorkSession.objects.create(
            company=self.company, branch=self.branch, job=self.job,
            employee=employee, assignment=assignment, service_name="Brake repair",
            labour_charge=Decimal("2200.00"), worker_charge=Decimal("500.00"),
            status="Approved", started_at=timezone.now(), elapsed_seconds=3600,
            approved_minutes=60,
        )
        JobWorkSession.objects.create(
            company=self.company, branch=self.branch, job=self.job,
            employee=employee, assignment=assignment, service_name="Pending tune-up",
            labour_charge=Decimal("3000.00"), worker_charge=Decimal("400.00"),
            status="PendingApproval", started_at=timezone.now(),
        )
        OutsideLabourCharge.objects.create(
            company=self.company, branch=self.branch, job=self.job,
            worker_name="External painter", work_description="Paint bumper",
            customer_charge=Decimal("4000.00"), worker_charge=Decimal("700.00"),
            status="Pending",
        )
        OutsideLabourCharge.objects.create(
            company=self.company, branch=self.branch, job=self.job,
            worker_name="Electrician", work_description="Electrical repair",
            customer_charge=Decimal("2000.00"), worker_charge=Decimal("300.00"),
            status="Paid",
        )
        part = StockItem.objects.create(
            company=self.company, branch=self.branch, name="Filter", sku="FILTER-1",
            cost_price=Decimal("80.00"), selling_price=Decimal("190.00"),
        )
        StockMovement.objects.create(
            company=self.company, branch=self.branch, job=self.job, item=part,
            movement_type="job_issue", quantity=Decimal("-3"),
            unit_cost=Decimal("80.00"),
        )
        StockMovement.objects.create(
            company=self.company, branch=self.branch, job=self.job, item=part,
            movement_type="return", quantity=Decimal("1"), unit_cost=Decimal("80.00"),
        )
        data = self.client.get(self.url)
        self.assertEqual(data.status_code, 200, data.data)
        self.assertEqual(data.data["mechanicCost"], "500.00")
        self.assertEqual(data.data["outsideStaffCost"], "1000.00")
        self.assertEqual(data.data["partsCost"], "160.00")
        self.assertEqual(data.data["outsidePending"], "700.00")
        self.assertEqual(data.data["totalCost"], "1660.00")
        self.assertFalse(data.data["partsAtCurrentCost"])
        self.assertTrue(data.data["unapprovedMechanicWork"])

    def test_legacy_stock_movements_flag_current_price_fallback(self):
        part = StockItem.objects.create(
            company=self.company, branch=self.branch, name="Old part", sku="OLD-PART",
            cost_price=Decimal("120.00"), selling_price=Decimal("250.00"),
        )
        StockMovement.objects.create(
            company=self.company, branch=self.branch, item=part, job=self.job,
            movement_type="job_issue", quantity=Decimal("-2"),
        )
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["partsCost"], "240.00")
        self.assertTrue(response.data["partsAtCurrentCost"])

    def test_omits_unallocated_wages_instead_of_counting_customer_charges(self):
        employee = Employee.objects.create(
            company=self.company, branch=self.branch,
            name="Daily mechanic", employee_code="DAY-01", payment_type="daily",
        )
        assignment = JobCardEmployeeAssignment.objects.create(
            company=self.company, branch=self.branch, job=self.job, employee=employee
        )
        JobWorkSession.objects.create(
            company=self.company, branch=self.branch, job=self.job,
            employee=employee, assignment=assignment, service_name="Check brakes",
            status="Approved", labour_charge=Decimal("1800.00"),
            worker_charge=Decimal("0.00"), started_at=timezone.now(),
            approved_minutes=60,
        )
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["mechanicCost"], "0.00")
        self.assertEqual(response.data["totalCost"], "0.00")
        self.assertTrue(response.data["unallocatedDailyWages"])

    def test_cost_data_requires_financial_and_stock_permissions(self):
        self.role.permissions = ["jobs.view"]
        self.role.save(update_fields=["permissions"])
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_other_company_job_cannot_be_read(self):
        outsider = Company.objects.create(name="Other Workshop", slug="different-cost-tenant")
        customer = Customer.objects.create(company=outsider, name="Other", phone="9876500000")
        vehicle = Vehicle.objects.create(company=outsider, customer=customer, registration="KL11AB4000")
        other = Job.objects.create(company=outsider, customer=customer, vehicle=vehicle, job_number="OUT-01")
        response = self.client.get(f"/api/v1/jobs/{other.id}/cost-summary")
        self.assertEqual(response.status_code, 404)

    def test_outside_shop_purchase_is_idempotent_and_added_to_direct_job_cost(self):
        self.role.permissions = ["jobs.view", "jobs.edit", "payroll.view", "expenses.view", "expenses.edit", "stock.view"]
        self.role.save(update_fields=["permissions"])
        url = f"/api/v1/job-cards/{self.job.id}/outside-parts"
        payload = {
            "requestId": "outside-part-req-0001",
            "partName": "Alternator belt",
            "sku": "ALT-BELT",
            "vendor": "Roadside Spare Shop",
            "invoiceNo": "BILL-021",
            "quantity": "2",
            "unitCost": "120.00",
            "sellingPrice": "190.00",
        }
        created = self.client.post(url, payload, format="json")
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(created.data["totalCost"], "0.00")
        self.assertEqual(created.data["status"], "Pending Approval")
        same = self.client.post(url, payload, format="json")
        self.assertEqual(same.status_code, 200, same.data)
        self.assertEqual(same.data["id"], created.data["id"])
        listed = self.client.get(url)
        self.assertEqual(listed.status_code, 200, listed.data)
        self.assertEqual(len(listed.data["items"]), 1)
        self.assertEqual(StockMovement.objects.filter(job=self.job).count(), 0)

        total = self.client.get(self.url)
        self.assertEqual(total.status_code, 200, total.data)
        self.assertEqual(total.data["outsidePartsCost"], "0.00")
        approved = self.client.post(
            f"{url}/{created.data['id']}/decision", {"decision": "approve"}, format="json",
        )
        self.assertEqual(approved.status_code, 200, approved.data)
        from django.core.files.uploadedfile import SimpleUploadedFile
        bill = SimpleUploadedFile(
            "shop-bill.pdf", b"%PDF-1.4\\n%%EOF", content_type="application/pdf"
        )
        completed = self.client.post(
            f"{url}/{created.data['id']}/complete",
            {"invoiceNo": "BILL-021", "bill": bill}, format="multipart",
        )
        self.assertEqual(completed.status_code, 200, getattr(completed, "data", completed.content[:1000]))
        self.assertEqual(completed.data["totalCost"], "240.00")
        total = self.client.get(self.url)
        self.assertEqual(total.data["outsidePartsCost"], "240.00")
        self.assertEqual(total.data["partsCost"], "240.00")
        self.assertEqual(total.data["totalCost"], "240.00")
        from .models import OutsidePartBill
        for doc in OutsidePartBill.objects.filter(job=self.job):
            doc.file.delete(save=False)

        invalid = self.client.post(url, {**payload, "requestId": "next-purchase", "quantity": "-1"}, format="json")
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(len(self.client.get(url).data["items"]), 1)
        self.job.status = Job.STATUS_DELIVERED
        self.job.save(update_fields=["status"])
        delivered = self.client.post(
            url, {**payload, "requestId": "after-delivery"}, format="json"
        )
        self.assertEqual(delivered.status_code, 400)

    def test_outside_shop_purchase_respects_job_edit_permission_and_tenant(self):
        url = f"/api/v1/job-cards/{self.job.id}/outside-parts"
        payload = {
            "requestId": "outside-not-allowed", "partName": "Filter",
            "vendor": "Shop", "quantity": "1", "unitCost": "10",
            "sellingPrice": "15",
        }
        result = self.client.post(url, payload, format="json")
        self.assertEqual(result.status_code, 403)
        self.assertEqual(self.client.get(url).status_code, 200)
        # The lookup must not expose another company's Job Card.
        other = Company.objects.create(name="Outside Tenant", slug="outside-parts-tenant")
        owner = Customer.objects.create(company=other, name="Foreign", phone="9090900000")
        car = Vehicle.objects.create(company=other, customer=owner, registration="KL08ZZ9900")
        foreign = Job.objects.create(company=other, customer=owner, vehicle=car, job_number="FOREIGN")
        self.assertEqual(
            self.client.get(f"/api/v1/job-cards/{foreign.id}/outside-parts").status_code,
            404
        )
