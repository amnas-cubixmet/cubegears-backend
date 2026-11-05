"""Regression checks for skipping inspection without fabricating checklist results."""
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.jobs.models import Job, JobActivity
from apps.roles.models import Role
from apps.vehicles.models import Vehicle


@override_settings(
    CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
    CREDIT_LABEL_ONLY_ACCESS_ENABLED=False,
)
class InspectionSkipTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Inspection Shop", slug="inspection-skip-shop")
        self.branch = Branch.objects.create(
            company=self.company, name="Main", code="MAIN", is_active=True,
        )
        self.role = Role.objects.create(
            company=self.company, name="Owner", code="OWNER_INSPECTION",
            permissions=["*"], is_active=True, is_system=True,
        )
        self.owner = User.objects.create_user(
            email="inspection-owner@example.test", password="safe-password",
            phone="+919876543220", name="Inspection Owner", company=self.company,
            branch=self.branch, role=self.role,
        )
        self.company.owner = self.owner
        self.company.save(update_fields=["owner"])
        self.customer = Customer.objects.create(
            company=self.company, branch=self.branch, name="Ali", phone="9876543220",
        )
        self.vehicle = Vehicle.objects.create(
            company=self.company, branch=self.branch, customer=self.customer,
            registration="KL10AB7272", make="Toyota", model="Etios",
        )
        self.job = Job.objects.create(
            company=self.company, branch=self.branch, customer=self.customer,
            vehicle=self.vehicle, job_number="JC-SKIP-1", status="Inspection",
            inspection={
                "status": "In Progress",
                "checklist": {"Engine noise": "Needs Attention"},
                "findings": [{"id": "F-1", "description": "Brake noise"}],
                "photos": [{"id": "P-1", "caption": "Front"}],
            },
            workflow_progress={"started": True, "completed": ["overview"]},
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)
        self.url = f"/api/v1/jobs/{self.job.pk}/inspection/skip"

    def test_skip_preserves_checks_and_advances_with_audit(self):
        response = self.client.post(self.url, {"reason": "Quick Service"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.job.refresh_from_db()
        self.assertEqual(self.job.inspection["status"], "Skipped")
        self.assertEqual(self.job.inspection["skipReason"], "Quick Service")
        self.assertEqual(self.job.inspection["checklist"]["Engine noise"], "Needs Attention")
        self.assertEqual(len(self.job.inspection["findings"]), 1)
        self.assertEqual(len(self.job.inspection["photos"]), 1)
        self.assertEqual(self.job.status, "Estimate Pending")
        self.assertIn("inspection", self.job.workflow_progress["skipped"])
        self.assertIn("inspection", self.job.workflow_progress["completed"])
        self.assertEqual(response.data["workflow"]["current"], "estimate")
        self.assertTrue(JobActivity.objects.filter(job=self.job, event="Inspection Skipped").exists())

    def test_skip_works_with_no_checks_at_all(self):
        self.job.inspection = {"status": "Not Started", "checklist": {}}
        self.job.save(update_fields=["inspection"])
        response = self.client.post(self.url, {"reason": "Inspection Not Required"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.job.refresh_from_db()
        self.assertEqual(self.job.inspection["checklist"], {})
        self.assertEqual(self.job.inspection["status"], "Skipped")

    def test_skip_requires_reason_and_cannot_be_repeated(self):
        invalid = self.client.post(self.url, {"reason": " "}, format="json")
        self.assertEqual(invalid.status_code, 400)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, "Inspection")
        good = self.client.post(self.url, {"reason": "Quick Service"}, format="json")
        self.assertEqual(good.status_code, 200)
        repeated = self.client.post(self.url, {"reason": "Quick Service"}, format="json")
        self.assertEqual(repeated.status_code, 400)
        self.assertEqual(JobActivity.objects.filter(job=self.job, event="Inspection Skipped").count(), 1)

    def test_completed_inspection_cannot_be_skipped(self):
        self.job.inspection = {"status": "Completed", "checklist": {"Engine noise": "Good"}}
        self.job.save(update_fields=["inspection"])
        result = self.client.post(self.url, {"reason": "Quick Service"}, format="json")
        self.assertEqual(result.status_code, 400)

    def test_skip_requires_current_inspection_stage(self):
        self.job.status = "New"
        self.job.workflow_progress = {}
        self.job.save(update_fields=["status", "workflow_progress"])
        result = self.client.post(self.url, {"reason": "Quick Service"}, format="json")
        self.assertEqual(result.status_code, 400)

    def test_other_workshop_cannot_skip_inspection(self):
        other = Company.objects.create(name="Other Shop", slug="another-inspection-shop")
        branch = Branch.objects.create(company=other, name="Other", code="OTHER", is_active=True)
        role = Role.objects.create(
            company=other, name="Owner", code="OTHER_INSPECTION",
            permissions=["*"], is_active=True, is_system=True,
        )
        user = User.objects.create_user(
            email="other-inspection@example.test", password="safe-password",
            phone="+919876543221", name="Other Owner", company=other, branch=branch, role=role,
        )
        other.owner = user
        other.save(update_fields=["owner"])
        self.client.force_authenticate(user=user)
        response = self.client.post(self.url, {"reason": "Quick Service"}, format="json")
        self.assertIn(response.status_code, (403, 404))
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, "Inspection")
