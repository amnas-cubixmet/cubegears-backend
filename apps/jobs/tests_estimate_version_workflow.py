"""Real quote versioning, retry, approval and audit workflow regressions."""

from decimal import Decimal
from uuid import uuid4

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.jobs.models import Job, JobActivity, JobEstimate
from apps.roles.models import Role
from apps.vehicles.models import Vehicle


@override_settings(CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
                   CREDIT_LABEL_ONLY_ACCESS_ENABLED=False)
class EstimateVersionWorkflowTests(TestCase):
    def setUp(self):
        company = Company.objects.create(
            name="Estimate Version Garage", slug="part09-estimate-garage",
        )
        branch = Branch.objects.create(company=company, name="Main", code="MAIN")
        role = Role.objects.create(company=company, name="Owner", code="PART09_OWNER",
                                   permissions=["*"], is_active=True)
        user = User.objects.create_user(
            email="part09-owner@example.test", password="test-pass-only",
            name="Owner", company=company, branch=branch, role=role,
        )
        company.owner = user
        company.save(update_fields=["owner"])
        customer = Customer.objects.create(
            company=company, branch=branch, name="Repair Customer",
            phone="+919877771111",
        )
        vehicle = Vehicle.objects.create(
            company=company, branch=branch, customer=customer,
            registration="KL10AB9090",
        )
        self.job = Job.objects.create(
            company=company, branch=branch, customer=customer, vehicle=vehicle,
            job_number="PART09-001", status="Estimate Pending",
            inspection={"status": "Completed"},
            workflow_progress={"completed": ["overview", "inspection"]},
        )
        self.client = APIClient()
        self.client.force_authenticate(user=user)
        self.user = user

    def path(self, suffix):
        base = f"/api/v1/jobs/{self.job.pk}"
        return f"{base}/{suffix}" if suffix else base

    def payload(self, *, request_id=None, price="250"):
        data = {
            "lines": [{"type": "Labour", "description": "Brake inspection",
                       "quantity": "2", "unitPrice": price}],
            "taxPercent": "0", "discount": "0",
        }
        if request_id:
            data["requestId"] = str(request_id)
        return data

    def test_retry_returns_same_draft_version_without_duplicate_activity(self):
        payload = self.payload(request_id=uuid4())
        first = self.client.post(self.path("estimates/quick"), payload, format="json")
        self.assertEqual(first.status_code, 201, first.data)
        second = self.client.post(self.path("estimates/quick"), payload, format="json")
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(first.data["id"], second.data["id"])
        self.assertEqual(first.data["version"], 1)
        self.assertEqual(self.job.estimates.count(), 1)
        self.assertEqual(JobActivity.objects.filter(
            job=self.job, event="Estimate Draft Saved",
        ).count(), 1)

    def test_reusing_request_id_for_changed_amount_is_blocked(self):
        token = uuid4()
        self.assertEqual(self.client.post(
            self.path("estimates/quick"), self.payload(request_id=token), format="json",
        ).status_code, 201)
        altered = self.client.post(
            self.path("estimates/quick"),
            self.payload(request_id=token, price="999"), format="json",
        )
        self.assertEqual(altered.status_code, 400, altered.data)
        self.assertEqual(self.job.estimates.count(), 1)
        self.assertEqual(self.job.estimates.get().total, Decimal("500.00"))

    def test_latest_version_is_bound_to_approval_and_audited(self):
        first = self.client.post(self.path("estimates/quick"), self.payload(), format="json")
        second = self.client.post(
            self.path("estimates/quick"), self.payload(price="700"), format="json",
        )
        self.assertEqual(second.status_code, 201, second.data)
        stale = self.client.post(self.path("workflow"), {
            "stage": "estimate", "approveEstimate": True,
            "estimateId": first.data["id"], "estimateVersion": first.data["version"],
        }, format="json")
        self.assertEqual(stale.status_code, 400, stale.data)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, "Estimate Pending")
        self.assertFalse(self.job.estimates.filter(status="Approved").exists())

        approved = self.client.post(self.path("workflow"), {
            "stage": "estimate", "approveEstimate": True,
            "estimateId": second.data["id"], "estimateVersion": second.data["version"],
        }, format="json")
        self.assertEqual(approved.status_code, 200, approved.data)
        self.job.refresh_from_db()
        decision = self.job.workflow_progress["estimateDecision"]
        self.assertEqual(decision["estimateId"], second.data["id"])
        self.assertEqual(decision["version"], 2)
        self.assertEqual(decision["confirmedByStaffId"], str(self.user.pk))
        self.assertEqual(decision["confirmationSource"], "workshop_staff_attestation")
        self.assertEqual(decision["total"], "1400.00")
        self.assertEqual(self.job.estimate_total, Decimal("1400.00"))
        self.assertEqual(self.job.estimates.filter(status="Approved").count(), 1)
        activity = JobActivity.objects.get(
            job=self.job, event="Workflow Stage Completed",
        )
        self.assertEqual(activity.metadata["estimateApproval"]["estimateId"],
                         second.data["id"])

    def test_draft_edits_reject_stale_versions(self):
        first = self.client.post(self.path("estimates/quick"), self.payload(), format="json")
        second = self.client.post(
            self.path("estimates/quick"), self.payload(price="400"), format="json",
        )
        stale = self.client.patch(
            self.path(f"estimates/quick/{first.data['id']}"),
            self.payload(price="999"), format="json",
        )
        self.assertEqual(stale.status_code, 400, stale.data)
        request = self.payload(request_id=uuid4(), price="450")
        updated = self.client.patch(
            self.path(f"estimates/quick/{second.data['id']}"), request, format="json",
        )
        self.assertEqual(updated.status_code, 200, updated.data)
        retry = self.client.patch(
            self.path(f"estimates/quick/{second.data['id']}"), request, format="json",
        )
        self.assertEqual(retry.status_code, 200, retry.data)
        self.assertEqual(JobActivity.objects.filter(
            job=self.job, event="Estimate Draft Updated",
        ).count(), 1)
        self.assertEqual(JobEstimate.objects.get(pk=second.data["id"]).total,
                         Decimal("900.00"))

    def test_replay_after_approval_does_not_create_new_version_or_work_cost(self):
        request = self.payload(request_id=uuid4())
        saved = self.client.post(self.path("estimates/quick"), request, format="json")
        self.client.post(self.path("workflow"), {
            "stage": "estimate", "approveEstimate": True,
            "estimateId": saved.data["id"],
        }, format="json")
        replay = self.client.post(self.path("estimates/quick"), request, format="json")
        self.assertEqual(replay.status_code, 200, replay.data)
        self.assertEqual(self.job.estimates.count(), 1)
        from apps.inventory.models import StockMovement
        from apps.payroll.models import JobWorkSession
        self.assertEqual(StockMovement.objects.filter(job=self.job).count(), 0)
        self.assertEqual(JobWorkSession.objects.filter(job=self.job).count(), 0)

    def test_generic_job_update_cannot_forge_approved_quote_total(self):
        before = self.job.estimate_total
        result = self.client.patch(self.path(""), {
            "estimate_total": "999999.99",
            "parts_total": "999999.99",
            "labour_total": "999999.99",
        }, format="json")
        self.assertEqual(result.status_code, 200, result.data)
        self.job.refresh_from_db()
        self.assertEqual(self.job.estimate_total, before)
        self.assertEqual(self.job.parts_total, Decimal("0.00"))
        self.assertEqual(self.job.labour_total, Decimal("0.00"))

    def test_posted_work_service_is_not_deleted_by_generic_job_patch(self):
        from uuid import uuid4 as new_uuid
        line = {
            "id": str(new_uuid()), "requestId": str(new_uuid()),
            "service": "Brake labour", "customerCharge": "500.00",
            "workerChargePosted": False,
        }
        self.job.status = "In Progress"
        self.job.work = [line]
        self.job.workflow_progress = {
            "completed": ["overview", "inspection", "estimate"],
        }
        self.job.save(update_fields=["status", "work", "workflow_progress"])
        result = self.client.patch(self.path(""), {"work": []}, format="json")
        self.assertEqual(result.status_code, 400, result.data)
        self.job.refresh_from_db()
        self.assertEqual(self.job.work, [line])

    def test_work_cannot_be_posted_before_approval(self):
        result = self.client.patch(self.path(""), {
            "work": [{"service": "Unapproved repair", "customerCharge": "800"}],
        }, format="json")
        self.assertEqual(result.status_code, 400, result.data)
        self.job.refresh_from_db()
        self.assertEqual(self.job.work, [])
