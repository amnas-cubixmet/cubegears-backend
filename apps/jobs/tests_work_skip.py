"""Verify Work stage skip is explicit, audited and never discards recorded costs."""
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.jobs.models import Job, JobActivity, OutsideLabourCharge
from apps.roles.models import Role
from apps.vehicles.models import Vehicle


@override_settings(
    CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
    CREDIT_LABEL_ONLY_ACCESS_ENABLED=False,
)
class WorkSkipTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Work Skip Garage", slug="work-skip-garage")
        self.branch = Branch.objects.create(
            company=self.company, name="Main", code="MAIN", is_active=True,
        )
        self.role = Role.objects.create(
            company=self.company, name="Owner", code="OWNER_WORK_SKIP",
            permissions=["*"], is_active=True, is_system=True,
        )
        self.owner = User.objects.create_user(
            email="work-skip-owner@example.test", password="safe-password",
            phone="+919876542211", name="Workshop Owner",
            company=self.company, branch=self.branch, role=self.role,
        )
        self.company.owner = self.owner
        self.company.save(update_fields=["owner"])
        customer = Customer.objects.create(
            company=self.company, branch=self.branch, name="Ali", phone="9876542211",
        )
        vehicle = Vehicle.objects.create(
            company=self.company, branch=self.branch, customer=customer,
            registration="KL10AB8899", make="Toyota", model="Innova",
        )
        self.job = Job.objects.create(
            company=self.company, branch=self.branch,
            customer=customer, vehicle=vehicle, job_number="SKIP-WORK-001",
            status="In Progress",
            workflow_progress={"started": True, "completed": ["overview", "inspection", "estimate"]},
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)
        self.url = f"/api/v1/jobs/{self.job.pk}/work/skip"

    def skip(self, reason="No Work Required"):
        return self.client.post(self.url, {"reason": reason}, format="json")

    def test_unworked_job_can_skip_to_qc_without_marking_work_done(self):
        response = self.skip()
        self.assertEqual(response.status_code, 200, getattr(response, "data", None))
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, "QC")
        self.assertEqual(response.data["current"], "qc")
        self.assertIn("work", response.data["skipped"])
        self.assertIn("work", self.job.workflow_progress["completed"])
        self.assertEqual(self.job.workflow_progress["skipDetails"]["work"]["reason"], "No Work Required")
        self.assertEqual(self.job.work, [])
        self.assertEqual(self.job.work_updates, [])
        self.assertTrue(JobActivity.objects.filter(job=self.job, event="Work Skipped").exists())

    def test_duplicate_skip_and_invalid_reason_are_rejected(self):
        for invalid in ("", "  ", "x" * 161, 2, {}):
            response = self.client.post(self.url, {"reason": invalid}, format="json")
            self.assertEqual(response.status_code, 400, getattr(response, "data", None))
        self.assertEqual(self.skip().status_code, 200)
        self.assertEqual(self.skip().status_code, 400)
        self.assertEqual(JobActivity.objects.filter(job=self.job, event="Work Skipped").count(), 1)

    def test_existing_work_notes_block_skip(self):
        self.job.work = [{"description": "Oil change"}]
        self.job.save(update_fields=["work"])
        response = self.skip()
        self.assertEqual(response.status_code, 400, getattr(response, "data", None))
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, "In Progress")

    def test_existing_outside_staff_charge_blocks_skip(self):
        OutsideLabourCharge.objects.create(
            company=self.company, branch=self.branch, job=self.job,
            worker_name="Painter", work_description="Paint repair",
            worker_charge="100.00",
        )
        response = self.skip()
        self.assertEqual(response.status_code, 400, getattr(response, "data", None))
        self.assertFalse(JobActivity.objects.filter(job=self.job, event="Work Skipped").exists())

    def test_already_advanced_job_cannot_skip(self):
        self.job.status = "QC"
        self.job.save(update_fields=["status"])
        response = self.skip()
        self.assertEqual(response.status_code, 400, getattr(response, "data", None))

    def test_read_only_staff_cannot_skip(self):
        read_role = Role.objects.create(
            company=self.company, name="Viewer", code="WORK_SKIP_VIEWER",
            permissions=["jobs.view"], is_active=True,
        )
        viewer = User.objects.create_user(
            email="work-skip-reader@example.test", password="safe-password",
            name="Viewer", company=self.company, branch=self.branch, role=read_role,
        )
        self.client.force_authenticate(user=viewer)
        response = self.skip()
        self.assertEqual(response.status_code, 403, getattr(response, "data", None))

    def test_another_workshop_cannot_skip(self):
        other = Company.objects.create(name="Different Garage", slug="different-work-skip")
        branch = Branch.objects.create(company=other, name="Other", code="OTHER", is_active=True)
        role = Role.objects.create(
            company=other, name="Owner", code="OWNER_OTHER_WORK",
            permissions=["*"], is_active=True, is_system=True,
        )
        user = User.objects.create_user(
            email="other-work-skip@example.test", password="safe-password",
            name="Other Owner", company=other, branch=branch, role=role,
        )
        other.owner = user
        other.save(update_fields=["owner"])
        self.client.force_authenticate(user=user)
        response = self.skip()
        self.assertIn(response.status_code, (403, 404))
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, "In Progress")
