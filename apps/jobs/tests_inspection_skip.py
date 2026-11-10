"""Regression tests for the distinct, permissioned Inspection Skip workflow."""
from copy import deepcopy

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
        self.company = Company.objects.create(name="Inspection Skip Garage", slug="inspection-skip-garage")
        self.branch = Branch.objects.create(
            company=self.company, name="Main", code="MAIN", is_active=True,
        )
        role = Role.objects.create(
            company=self.company, name="Owner", code="OWNER_INSPECTION_SKIP",
            permissions=["*"], is_active=True, is_system=True,
        )
        self.owner = User.objects.create_user(
            email="inspection-skip-owner@example.test", password="test-password",
            phone="+919876543210", name="Inspection Owner",
            company=self.company, branch=self.branch, role=role,
        )
        self.company.owner = self.owner
        self.company.save(update_fields=["owner"])
        self.customer = Customer.objects.create(
            company=self.company, branch=self.branch,
            name="Inspection Customer", phone="+919999998888",
        )
        self.vehicle = Vehicle.objects.create(
            company=self.company, branch=self.branch, customer=self.customer,
            registration="KL10AB1111", make="Hyundai", model="i20",
        )
        self.job = Job.objects.create(
            company=self.company, branch=self.branch, customer=self.customer,
            vehicle=self.vehicle, job_number="SKIP-001",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)

    def url(self, ending):
        return f"/api/v1/jobs/{self.job.pk}/{ending}"

    def open_inspection(self):
        response = self.client.post(self.url("workflow"), {"stage": "overview"}, format="json")
        self.assertEqual(response.status_code, 200, getattr(response, "data", None))
        self.job.refresh_from_db()
        self.assertEqual(response.data["current"], "inspection")

    def skip(self, reason="Quick Service"):
        return self.client.post(self.url("inspection/skip"), {"reason": reason}, format="json")

    def test_skipped_inspection_can_be_reopened_and_edited_without_erasing_audit(self):
        self.open_inspection()
        self.assertEqual(self.skip().status_code, 200)
        reply = self.client.post(self.url("inspection/reopen"), {}, format="json")
        self.assertEqual(reply.status_code, 200, getattr(reply, "data", None))
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, Job.STATUS_INSPECTION)
        self.assertEqual(self.job.inspection["status"], "In Progress")
        self.assertNotIn("inspection", reply.data["workflow"]["skipped"])
        self.assertEqual(reply.data["workflow"]["current"], "inspection")
        self.assertEqual(self.job.inspection["skipHistory"][0]["skipReason"], "Quick Service")
        self.assertEqual(JobActivity.objects.filter(job=self.job, event="Inspection Skipped").count(), 1)
        self.assertEqual(JobActivity.objects.filter(job=self.job, event="Inspection Reopened").count(), 1)
        edit = self.client.patch(self.url("inspection/checklist"), {
            "itemName": "Engine oil", "status": "Good",
        }, format="json")
        self.assertEqual(edit.status_code, 200, getattr(edit, "data", None))
        self.assertEqual(edit.data["checklist"]["Engine oil"], "Good")
        self.assertEqual(self.client.post(self.url("inspection/reopen"), {}, format="json").status_code, 400)

    def test_both_skipped_stages_must_be_reopened_in_reverse_order(self):
        self.open_inspection()
        self.assertEqual(self.skip().status_code, 200)
        skipped = self.client.post(self.url("estimate/skip"), {
            "reason": "Quick Service", "details": "",
        }, format="json")
        self.assertEqual(skipped.status_code, 200, getattr(skipped, "data", None))
        self.assertEqual(skipped.data["current"], "work")
        self.assertEqual(self.client.post(self.url("inspection/reopen"), {}, format="json").status_code, 400)
        reopened_estimate = self.client.post(self.url("estimate/reopen"), {}, format="json")
        self.assertEqual(reopened_estimate.status_code, 200, getattr(reopened_estimate, "data", None))
        self.assertEqual(reopened_estimate.data["current"], "estimate")
        self.assertNotIn("estimate", reopened_estimate.data["skipped"])
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, Job.STATUS_ESTIMATE_PENDING)
        self.assertEqual(self.job.workflow_progress["estimateSkipHistory"][0]["reason"], "Quick Service")
        self.assertEqual(self.job.workflow_progress["estimateDecision"]["status"], "Pending")
        quote = self.client.post(self.url("estimates/quick"), {
            "lines": [{"type": "Labour", "description": "Reopened diagnosis",
                       "quantity": "1", "unitPrice": "250"}],
            "taxPercent": "0", "discount": "0",
        }, format="json")
        self.assertEqual(quote.status_code, 201, getattr(quote, "data", None))
        self.assertEqual(self.client.post(self.url("inspection/reopen"), {}, format="json").status_code, 200)
        self.assertEqual(JobActivity.objects.filter(job=self.job, event="Estimate Skipped").count(), 1)
        self.assertEqual(JobActivity.objects.filter(job=self.job, event="Estimate Reopened").count(), 1)

    def test_estimate_reopen_is_blocked_once_work_is_recorded_or_qc_started(self):
        self.open_inspection()
        self.assertEqual(self.skip().status_code, 200)
        self.assertEqual(self.client.post(self.url("estimate/skip"), {
            "reason": "Estimate Not Required",
        }, format="json").status_code, 200)
        self.job.refresh_from_db()
        self.job.work = [{"description": "Labour started"}]
        self.job.save(update_fields=["work"])
        response = self.client.post(self.url("estimate/reopen"), {}, format="json")
        self.assertEqual(response.status_code, 400)
        self.job.refresh_from_db()
        self.assertEqual(self.job.workflow_progress["estimateDecision"]["status"], "Skipped")
        self.assertFalse(JobActivity.objects.filter(job=self.job, event="Estimate Reopened").exists())
        self.job.work = []
        self.job.status = Job.STATUS_QC
        self.job.save(update_fields=["work", "status"])
        self.assertEqual(self.client.post(self.url("estimate/reopen"), {}, format="json").status_code, 400)

    def test_read_only_staff_cannot_reopen_skipped_stages(self):
        self.open_inspection()
        self.assertEqual(self.skip().status_code, 200)
        role = Role.objects.create(
            company=self.company, name="Read only", code="VIEW_SKIP_REOPEN",
            permissions=["jobs.view"], is_active=True,
        )
        viewer = User.objects.create_user(
            email="skip-reopen-viewer@example.test", password="safe-password",
            name="Viewer", company=self.company, branch=self.branch, role=role,
        )
        self.client.force_authenticate(user=viewer)
        self.assertEqual(self.client.post(self.url("inspection/reopen"), {}, format="json").status_code, 403)

    def test_skip_before_start_with_zero_checks_is_distinct_and_audited(self):
        self.open_inspection()
        before = self.client.get(self.url("inspection"))
        self.assertEqual(before.status_code, 200)
        self.assertEqual(before.data["status"], "Not Started")
        self.assertTrue(before.data["skipAllowed"])
        result = self.skip()
        self.assertEqual(result.status_code, 200, getattr(result, "data", None))
        self.job.refresh_from_db()
        self.assertEqual(self.job.inspection["status"], "Skipped")
        self.assertEqual(self.job.inspection["skipReason"], "Quick Service")
        self.assertEqual(self.job.inspection["skippedById"], str(self.owner.pk))
        self.assertEqual(self.job.inspection["skippedBy"], self.owner.name)
        self.assertTrue(self.job.inspection["skippedAt"])
        self.assertEqual(self.job.inspection.get("checklist", {}), {})
        self.assertEqual(self.job.status, Job.STATUS_ESTIMATE_PENDING)
        self.assertEqual(result.data["workflow"]["current"], "estimate")
        self.assertEqual(result.data["workflow"]["inspectionStatus"], "Skipped")
        self.assertIn("inspection", result.data["workflow"]["skipped"])
        event = JobActivity.objects.get(job=self.job, event="Inspection Skipped")
        self.assertEqual(event.actor, self.owner)
        self.assertEqual(event.metadata["reason"], "Quick Service")
        self.assertEqual(event.metadata["staffId"], str(self.owner.pk))

    def test_partial_results_findings_diagnostics_photos_and_other_data_survive(self):
        self.open_inspection()
        original = {
            "status": "In Progress",
            "startedAt": "2026-11-01T09:00:00+05:30",
            "checklist": {"Engine oil": "Good", "Brake pads": "Not Checked"},
            "findings": [{"id": "FND-1", "description": "Scratch", "severity": "Low"}],
            "photos": [{"id": "PH-1", "caption": "Existing scratch"}],
            "diagnosticScan": {"performed": True, "codes": [{"code": "P0001"}]},
        }
        self.job.inspection = deepcopy(original)
        self.job.work = [{"description": "Preserved work"}]
        self.job.qc = {"status": "Pending"}
        self.job.save(update_fields=["inspection", "work", "qc"])
        response = self.skip("Customer Declined Inspection")
        self.assertEqual(response.status_code, 200, getattr(response, "data", None))
        self.job.refresh_from_db()
        for key in ("startedAt", "checklist", "findings", "photos", "diagnosticScan"):
            self.assertEqual(self.job.inspection[key], original[key])
        self.assertEqual(self.job.work, [{"description": "Preserved work"}])
        self.assertEqual(self.job.qc, {"status": "Pending"})
        self.assertNotIn("completedAt", self.job.inspection)
        self.assertEqual(self.job.inspection["checklist"]["Brake pads"], "Not Checked")

    def test_started_incomplete_inspection_can_skip_without_any_good_items(self):
        self.open_inspection()
        start = self.client.post(self.url("inspection/start"), {}, format="json")
        self.assertEqual(start.status_code, 200, getattr(start, "data", None))
        result = self.skip("Inspection Not Required")
        self.assertEqual(result.status_code, 200, getattr(result, "data", None))
        self.assertEqual(result.data["inspection"]["status"], "Skipped")

    def test_already_skipped_cannot_create_duplicate_event_or_be_started_or_completed(self):
        self.open_inspection()
        self.assertEqual(self.skip().status_code, 200)
        self.assertEqual(self.skip().status_code, 400)
        self.assertEqual(self.client.post(self.url("inspection/start"), {}, format="json").status_code, 400)
        self.assertEqual(self.client.post(self.url("inspection/complete"), {}, format="json").status_code, 400)
        self.assertEqual(JobActivity.objects.filter(job=self.job, event="Inspection Skipped").count(), 1)

    def test_completed_inspection_cannot_be_skipped(self):
        self.open_inspection()
        self.assertEqual(self.client.post(self.url("inspection/start"), {}, format="json").status_code, 200)
        self.assertEqual(self.client.patch(self.url("inspection/checklist"), {
            "itemName": "Brake pedal", "status": "Good",
        }, format="json").status_code, 200)
        complete = self.client.post(self.url("inspection/complete"), {}, format="json")
        self.assertEqual(complete.status_code, 200, getattr(complete, "data", None))
        self.assertEqual(complete.data["inspection"]["status"], "Completed")
        self.assertEqual(self.skip().status_code, 400)
        self.assertFalse(JobActivity.objects.filter(job=self.job, event="Inspection Skipped").exists())

    def test_not_applicable_does_not_count_as_good_for_completion(self):
        self.open_inspection()
        self.assertEqual(self.client.post(self.url("inspection/start"), {}, format="json").status_code, 200)
        na = self.client.patch(self.url("inspection/checklist"), {
            "itemName": "Cabin filter", "status": "Not Applicable",
        }, format="json")
        self.assertEqual(na.status_code, 200, getattr(na, "data", None))
        self.assertEqual(na.data["checklist"]["Cabin filter"], "Not Applicable")
        complete = self.client.post(self.url("inspection/complete"), {}, format="json")
        self.assertEqual(complete.status_code, 400)
        self.assertEqual(self.job.inspection.get("status", "Not Started"), "Not Started")
        self.job.refresh_from_db()
        self.assertEqual(self.job.inspection["status"], "In Progress")
        self.assertEqual(self.job.inspection["checklist"]["Cabin filter"], "Not Applicable")

    def test_skip_requires_valid_reason_and_overview_completion(self):
        self.assertEqual(self.skip().status_code, 400)
        self.open_inspection()
        for reason in ("", "  ", "x" * 161, 5, {}):
            result = self.client.post(self.url("inspection/skip"), {"reason": reason}, format="json")
            self.assertEqual(result.status_code, 400, getattr(result, "data", None))
        self.job.refresh_from_db()
        self.assertNotEqual(self.job.inspection.get("status"), "Skipped")

    def test_company_policy_blocks_skip_and_is_reflected_in_inspection_detail(self):
        self.open_inspection()
        self.company.require_vehicle_inspection = True
        self.company.save(update_fields=["require_vehicle_inspection"])
        details = self.client.get(self.url("inspection"))
        self.assertEqual(details.status_code, 200)
        self.assertFalse(details.data["skipAllowed"])
        self.assertIn("policy", details.data["skipBlockedReason"].lower())
        response = self.skip()
        self.assertEqual(response.status_code, 400)
        self.assertFalse(JobActivity.objects.filter(job=self.job, event="Inspection Skipped").exists())

    def test_mandatory_safety_and_critical_checks_block_skip(self):
        self.open_inspection()
        for item in [
            {"mandatorySafetyInspection": True},
            {"checklist": {"Brake pads": "Critical"}},
            {"findings": [{"severity": "Critical", "description": "Brake failure"}]},
        ]:
            self.job.inspection = {"status": "In Progress", **item}
            self.job.save(update_fields=["inspection"])
            response = self.skip()
            self.assertEqual(response.status_code, 400, getattr(response, "data", None))
        self.assertFalse(JobActivity.objects.filter(job=self.job, event="Inspection Skipped").exists())

    def test_read_only_staff_cannot_skip(self):
        self.open_inspection()
        role = Role.objects.create(
            company=self.company, name="Reader", code="INSPECTION_READER",
            permissions=["jobs.view"], is_active=True,
        )
        viewer = User.objects.create_user(
            email="inspection-reader@example.test", password="test-password",
            name="Read Only", company=self.company, branch=self.branch, role=role,
        )
        self.client.force_authenticate(user=viewer)
        self.assertEqual(self.client.get(self.url("inspection")).status_code, 200)
        details = self.client.get(self.url("inspection"))
        self.assertFalse(details.data["skipAllowed"])
        response = self.skip()
        self.assertEqual(response.status_code, 403, getattr(response, "data", None))

    def test_other_tenant_job_is_not_found(self):
        self.open_inspection()
        foreign = Company.objects.create(name="Foreign Garage", slug="inspection-other-tenant")
        role = Role.objects.create(
            company=foreign, name="Owner", code="FOREIGN_INSPECTION_OWNER",
            permissions=["*"], is_active=True,
        )
        user = User.objects.create_user(
            email="foreign-inspection@example.test", password="test-password",
            name="Foreign", company=foreign, role=role,
        )
        foreign.owner = user
        foreign.save(update_fields=["owner"])
        self.client.force_authenticate(user=user)
        self.assertEqual(self.skip().status_code, 404)

    def test_generic_job_patch_cannot_forge_skipped_or_completed_status(self):
        self.open_inspection()
        for status_value in ("Skipped", "Completed"):
            result = self.client.patch(
                f"/api/v1/jobs/{self.job.pk}", {"inspection": {"status": status_value}},
                format="json",
            )
            self.assertEqual(result.status_code, 400, getattr(result, "data", None))
        self.job.refresh_from_db()
        self.assertNotIn(self.job.inspection.get("status"), ("Skipped", "Completed"))
