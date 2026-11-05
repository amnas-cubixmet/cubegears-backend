"""Manual complaint decisions must not be silently completed by Work/QC."""
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.jobs.models import Job, JobActivity
from apps.roles.models import Role
from apps.vehicles.models import Vehicle


@override_settings(CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
                   CREDIT_LABEL_ONLY_ACCESS_ENABLED=False)
class ManualComplaintTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Complaint Workshop", slug="complaint-workshop")
        self.branch = Branch.objects.create(company=self.company, name="Main", code="MAIN")
        role = Role.objects.create(company=self.company, name="Owner", code="COMPLAINT_OWNER",
                                   permissions=["*"], is_active=True)
        self.owner = User.objects.create_user(
            email="complaint-owner@example.test", password="test-pass",
            name="Owner", company=self.company, branch=self.branch, role=role,
        )
        self.company.owner = self.owner
        self.company.save(update_fields=["owner"])
        customer = Customer.objects.create(company=self.company, branch=self.branch,
                                           name="Customer", phone="+919999999999")
        vehicle = Vehicle.objects.create(company=self.company, branch=self.branch,
                                         customer=customer, registration="KL10AA1212")
        self.job = Job.objects.create(
            company=self.company, branch=self.branch, customer=customer,
            vehicle=vehicle, job_number="WORK-001", status="In Progress",
            workflow_progress={"completed": ["overview", "inspection", "estimate"]},
            complaints=[
                {"id": "CMP-1", "description": "Brake noise", "status": "Open"},
                {"id": "CMP-2", "description": "AC not cooling", "status": "Completed"},
            ],
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)

    def url(self, path):
        return f"/api/v1/jobs/{self.job.pk}/{path}"

    def update(self, key="CMP-1", status="In Progress", notes="Checked brakes", **kwargs):
        return self.client.post(
            self.url(f"complaints/{key}/status"),
            {"status": status, "notes": notes, **kwargs}, format="json",
        )

    def test_legacy_auto_completed_is_not_inferred_as_resolved(self):
        reply = self.client.get(self.url("").rstrip("/"))
        self.assertEqual(reply.status_code, 200, reply.data)
        statuses = [row["status"] for row in reply.data["complaints"]]
        self.assertEqual(statuses, ["Pending", "Pending"])

    def test_manual_status_is_audited_kept_after_work_and_duplicate_is_idempotent(self):
        reply = self.update()
        self.assertEqual(reply.status_code, 200, reply.data)
        self.assertEqual(reply.data["complaint"]["status"], "In Progress")
        self.assertEqual(len(reply.data["complaint"]["statusHistory"]), 1)
        self.assertEqual(reply.data["complaint"]["statusUpdatedBy"], str(self.owner.pk))
        self.assertEqual(self.update().status_code, 200)
        self.assertEqual(JobActivity.objects.filter(
            job=self.job, event="Complaint Status Updated",
        ).count(), 1)
        self.assertEqual(self.client.post(self.url("workflow"), {
            "stage": "work", "confirmWorkDone": True,
        }, format="json").status_code, 400)
        # An arbitrary truthy string must never count as recorded consent.
        not_acknowledged = self.client.post(self.url("workflow"), {
            "stage": "work", "confirmWorkDone": True,
            "confirmUnresolvedComplaints": "true",
        }, format="json")
        self.assertEqual(not_acknowledged.status_code, 400)
        ack = self.client.post(self.url("workflow"), {
            "stage": "work", "confirmWorkDone": True,
            "confirmUnresolvedComplaints": True,
        }, format="json")
        self.assertEqual(ack.status_code, 200, ack.data)
        self.job.refresh_from_db()
        self.assertEqual(self.job.complaints[0]["status"], "In Progress")
        self.assertEqual(self.job.complaints[1]["status"], "Completed")
        self.assertEqual(self.job.workflow_progress["complaintReview"]["unresolvedCount"], 2)

    def test_resolved_and_not_applicable_require_real_staff_decision(self):
        self.assertEqual(self.update(status="Resolved", notes="Replaced brake pads").status_code, 200)
        self.assertEqual(self.update("CMP-2", status="Not Applicable", notes="").status_code, 400)
        self.assertEqual(self.update("CMP-2", status="Not Applicable",
                                     notes="Issue unrelated to this job").status_code, 200)
        done = self.client.post(self.url("workflow"), {
            "stage": "work", "confirmWorkDone": True,
        }, format="json")
        self.assertEqual(done.status_code, 200, done.data)
        self.job.refresh_from_db()
        self.assertEqual([r["status"] for r in self.job.complaints],
                         ["Resolved", "Not Applicable"])
        self.assertNotIn("complaintReview", self.job.workflow_progress)

    def test_generic_job_patch_cannot_forge_resolution_or_wipe_history(self):
        self.assertEqual(self.update(status="Resolved", notes="Brake repaired").status_code, 200)
        self.job.refresh_from_db()
        row = dict(self.job.complaints[0])
        row.update({"status": "Not Applicable", "statusHistory": [],
                    "statusUpdatedBy": "forged", "resolutionNotes": "Fake"})
        reply = self.client.patch(self.url("").rstrip("/"), {
            "complaints": [row, self.job.complaints[1]],
        }, format="json")
        self.assertEqual(reply.status_code, 200, reply.data)
        self.job.refresh_from_db()
        self.assertEqual(self.job.complaints[0]["status"], "Resolved")
        self.assertEqual(self.job.complaints[0]["statusUpdatedBy"], str(self.owner.pk))
        self.assertEqual(len(self.job.complaints[0]["statusHistory"]), 1)

    def test_unapproved_roles_and_other_workshops_are_rejected(self):
        read_role = Role.objects.create(company=self.company, name="Reader",
                                        code="COMPLAINT_READER",
                                        permissions=["jobs.view"], is_active=True)
        reader = User.objects.create_user(
            email="complaint-reader@example.test", password="test-pass",
            name="Viewer", company=self.company, role=read_role,
        )
        self.client.force_authenticate(user=reader)
        self.assertEqual(self.update().status_code, 403)
        foreign = Company.objects.create(name="Another", slug="another-complaint-workshop")
        foreign_role = Role.objects.create(company=foreign, name="Owner",
                                           code="FOREIGN_COMPLAINT_OWNER",
                                           permissions=["*"], is_active=True)
        user = User.objects.create_user(
            email="other-complaint@example.test", password="test-pass",
            name="Another", company=foreign, role=foreign_role,
        )
        self.client.force_authenticate(user=user)
        self.assertEqual(self.update().status_code, 404)

    def test_status_validation_and_legacy_row_token(self):
        self.assertEqual(self.update(status="Completed").status_code, 400)
        self.assertEqual(self.update(status="Not Resolved", notes="").status_code, 400)
        self.job.complaints[0].pop("id")
        self.job.save(update_fields=["complaints"])
        reply = self.update("row-0", status="Pending", notes="Needs review")
        self.assertEqual(reply.status_code, 200, reply.data)
        self.assertTrue(reply.data["complaint"]["id"])
