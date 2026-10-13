from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.roles.models import Role
from apps.vehicles.models import Vehicle
from .models import Job


class QCInspectorLoginTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="QC Workshop", slug="qc-login-inspector")
        branch = Branch.objects.create(company=self.company, name="Main", code="QC")
        role = Role.objects.create(
            company=self.company, name="QC Operator", code="QC_OPERATOR",
            permissions=["jobs.view", "jobs.edit"],
        )
        self.first = User.objects.create(
            company=self.company, branch=branch, role=role,
            name="First Inspector", email="first-inspector@qc.local",
        )
        self.second = User.objects.create(
            company=self.company, branch=branch, role=role,
            name="Second Inspector", email="second-inspector@qc.local",
        )
        customer = Customer.objects.create(
            company=self.company, branch=branch, name="Customer", phone="9876500123",
        )
        vehicle = Vehicle.objects.create(
            company=self.company, branch=branch, customer=customer, registration="KL10AB1000",
        )
        self.job = Job.objects.create(
            company=self.company, branch=branch, customer=customer,
            vehicle=vehicle, job_number="QC-LOGIN-001",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.first)
        self.url = f"/api/v1/jobs/{self.job.pk}"

    def test_qc_save_uses_logged_in_user_instead_of_client_inspector(self):
        result = self.client.patch(self.url, {
            "qc": {
                "inspector": "Unrelated Person",
                "inspectorUserId": "forged-id",
                "status": "Pending",
                "testDriveNotes": "Road test scheduled.",
                "checklist": [{"id": "QC-1", "item": "Road Test", "status": "Pending"}],
            },
        }, format="json")
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data["qc"]["inspector"], self.first.name)
        self.assertEqual(result.data["qc"]["inspectorUserId"], str(self.first.id))
        self.assertEqual(result.data["qc"]["testDriveNotes"], "Road test scheduled.")
        self.job.refresh_from_db()
        self.assertEqual(self.job.qc["inspector"], self.first.name)
        self.assertEqual(self.job.qc["inspectorUserId"], str(self.first.id))

    def test_subsequent_qc_editor_is_current_authenticated_inspector(self):
        initial = self.client.patch(
            self.url, {"qc": {"status": "Pending", "inspector": "Claimed Inspector"}},
            format="json",
        )
        self.assertEqual(initial.status_code, 200, initial.data)
        self.client.force_authenticate(user=self.second)
        changed = self.client.patch(self.url, {
            "qc": {"status": "Pass", "inspector": self.first.name,
                   "checklist": [{"id": "QC-1", "item": "Road Test", "status": "Pass"}]},
        }, format="json")
        self.assertEqual(changed.status_code, 200, changed.data)
        self.assertEqual(changed.data["qc"]["inspector"], self.second.name)
        self.assertEqual(changed.data["qc"]["inspectorUserId"], str(self.second.id))

    def test_unrelated_job_updates_do_not_overwrite_saved_inspector(self):
        saved = self.client.patch(self.url, {
            "qc": {"status": "Pending", "inspector": "Client-Supplied Name"},
        }, format="json")
        self.assertEqual(saved.status_code, 200, saved.data)
        self.client.force_authenticate(user=self.second)
        updated = self.client.patch(
            self.url, {"notes": "Customer needs pickup confirmation."}, format="json",
        )
        self.assertEqual(updated.status_code, 200, updated.data)
        self.assertEqual(updated.data["qc"]["inspector"], self.first.name)
        self.assertEqual(updated.data["qc"]["inspectorUserId"], str(self.first.id))

    def test_inspector_requires_named_authenticated_account(self):
        self.first.name = ""
        self.first.save(update_fields=["name"])
        failed = self.client.patch(
            self.url, {"qc": {"status": "Pending", "inspector": "Faked Name"}},
            format="json",
        )
        self.assertEqual(failed.status_code, 400, failed.data)
        self.assertIn("qc", failed.data)
        self.job.refresh_from_db()
        self.assertEqual(self.job.qc, {})

    def test_qc_must_be_object(self):
        failed = self.client.patch(self.url, {"qc": ["not", "an", "object"]}, format="json")
        self.assertEqual(failed.status_code, 400, failed.data)
        self.assertIn("qc", failed.data)
