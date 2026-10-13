from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.roles.models import Role
from apps.vehicles.models import Vehicle
from .models import Job, JobActivity, OutsidePartBill


class OutsidePartsApprovalTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Parts Shop", slug="part-approval-test")
        self.branch = Branch.objects.create(company=self.company, name="Main", code="MAIN")
        self.role = Role.objects.create(
            company=self.company, name="Manager", code="MANAGER",
            permissions=["jobs.view", "jobs.edit", "expenses.view", "expenses.edit",
                         "payroll.view", "stock.view"],
        )
        self.user = User.objects.create(
            company=self.company, branch=self.branch, role=self.role,
            name="Manager", email="purchase@parts.local",
        )
        customer = Customer.objects.create(
            company=self.company, branch=self.branch,
            name="Customer", phone="9988776655",
        )
        car = Vehicle.objects.create(
            company=self.company, branch=self.branch,
            customer=customer, registration="KL10AB2400",
        )
        self.job = Job.objects.create(
            company=self.company, branch=self.branch,
            customer=customer, vehicle=car, job_number="PART-REQUEST-1",
            status=Job.STATUS_IN_PROGRESS,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.base = f"/api/v1/job-cards/{self.job.pk}/outside-parts"
        self.cost = f"/api/v1/jobs/{self.job.pk}/cost-summary"
        self.payload = {
            "requestId": "REQUEST-01", "partName": "Engine air filter",
            "vendor": "Outside Spare Shop", "quantity": "2",
            "unitCost": "100.00", "sellingPrice": "170.00",
        }

    def tearDown(self):
        for doc in OutsidePartBill.objects.filter(company=self.company):
            if doc.file:
                doc.file.delete(save=False)

    def request_part(self):
        r = self.client.post(self.base, self.payload, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        return r.data

    @staticmethod
    def pdf(contents=b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF"):
        return SimpleUploadedFile("invoice.pdf", contents, content_type="application/pdf")

    def test_request_approval_bill_and_cost_stages(self):
        requested = self.request_part()
        self.assertEqual(requested["status"], "Pending Approval")
        self.assertEqual(requested["totalCost"], "0.00")
        self.assertEqual(self.client.get(self.cost).data["outsidePartsCost"], "0.00")

        direct = self.client.post(
            f"{self.base}/{requested['id']}/complete",
            {"invoiceNo": "BILL-1", "bill": self.pdf()}, format="multipart",
        )
        self.assertEqual(direct.status_code, 400)
        self.assertEqual(OutsidePartBill.objects.count(), 0)

        approved = self.client.post(
            f"{self.base}/{requested['id']}/decision",
            {"decision": "approve"}, format="json",
        )
        self.assertEqual(approved.status_code, 200, approved.data)
        self.assertEqual(approved.data["status"], "Approved")
        self.assertEqual(self.client.get(self.cost).data["outsidePartsCost"], "0.00")

        overpriced = self.client.post(
            f"{self.base}/{requested['id']}/complete",
            {"invoiceNo": "BILL-1", "unitCost": "110", "bill": self.pdf()},
            format="multipart",
        )
        self.assertEqual(overpriced.status_code, 400, overpriced.data)
        invalid_file = self.client.post(
            f"{self.base}/{requested['id']}/complete",
            {"invoiceNo": "BILL-1", "bill": self.pdf(b"not actually a PDF")},
            format="multipart",
        )
        self.assertEqual(invalid_file.status_code, 400, invalid_file.data)

        from django.test import override_settings
        with override_settings(DEBUG=True):
            purchased = self.client.post(
                f"{self.base}/{requested['id']}/complete",
                {"invoiceNo": "BILL-1", "unitCost": "95.00", "bill": self.pdf()},
                format="multipart",
            )
        self.assertEqual(purchased.status_code, 200, getattr(purchased, "data", purchased.content[:1000]))
        self.assertEqual(purchased.data["status"], "Purchased")
        self.assertEqual(purchased.data["totalCost"], "190.00")
        self.assertTrue(purchased.data["billId"])
        self.assertEqual(self.client.get(self.cost).data["outsidePartsCost"], "190.00")

        downloaded = self.client.get(f"{self.base}/{requested['id']}/bill")
        self.assertEqual(downloaded.status_code, 200)
        self.assertEqual(downloaded["Cache-Control"], "private, no-store")
        self.assertIn("application/pdf", downloaded["Content-Type"])
        self.assertTrue(b"".join(downloaded.streaming_content).startswith(b"%PDF-"))

        repeated = self.client.post(
            f"{self.base}/{requested['id']}/complete",
            {"invoiceNo": "BILL-1", "bill": self.pdf()},
            format="multipart",
        )
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(OutsidePartBill.objects.count(), 1)
        self.assertEqual(JobActivity.objects.filter(job=self.job, event="Outside Part Purchased").count(), 1)

    def test_duplicate_requests_and_rejection_do_not_change_cost(self):
        item = self.request_part()
        again = self.client.post(self.base, self.payload, format="json")
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.data["id"], item["id"])
        rejected = self.client.post(
            f"{self.base}/{item['id']}/decision",
            {"decision": "reject"}, format="json",
        )
        self.assertEqual(rejected.status_code, 200)
        self.assertEqual(rejected.data["status"], "Rejected")
        self.assertEqual(self.client.get(self.cost).data["totalCost"], "0.00")
        self.assertEqual(self.client.post(
            f"{self.base}/{item['id']}/decision",
            {"decision": "approve"}, format="json",
        ).status_code, 400)

    def test_manager_approval_requires_financial_permission(self):
        item = self.request_part()
        self.role.permissions = ["jobs.view", "jobs.edit", "expenses.view"]
        self.role.save(update_fields=["permissions"])
        decision = self.client.post(
            f"{self.base}/{item['id']}/decision",
            {"decision": "approve"}, format="json",
        )
        self.assertEqual(decision.status_code, 403)
        self.assertEqual(self.client.get(self.cost).status_code, 403)

    def test_cannot_modify_approval_by_generic_job_patch(self):
        requested = self.request_part()
        altered = self.client.patch(
            f"/api/v1/jobs/{self.job.pk}",
            {"partsWorkflow": {"outsideParts": [{**requested, "status": "Purchased", "totalCost": "99999"}]}},
            format="json",
        )
        self.assertEqual(altered.status_code, 200, altered.data)
        self.job.refresh_from_db()
        self.assertEqual(self.job.parts_workflow["outsideParts"][0]["status"], "Pending Approval")

    def test_unrelated_company_cannot_access_invoice_or_approval(self):
        other = Company.objects.create(name="Other Tenant", slug="parts-other-tenant")
        other_branch = Branch.objects.create(company=other, name="Main", code="OTHER")
        other_customer = Customer.objects.create(company=other, branch=other_branch, name="Other", phone="9090900123")
        other_car = Vehicle.objects.create(company=other, branch=other_branch, customer=other_customer, registration="KL12AB9901")
        other_job = Job.objects.create(
            company=other, branch=other_branch, customer=other_customer,
            vehicle=other_car, job_number="FOREIGN-01",
        )
        url = f"/api/v1/job-cards/{other_job.pk}/outside-parts"
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url, self.payload, format="json").status_code, 404)
