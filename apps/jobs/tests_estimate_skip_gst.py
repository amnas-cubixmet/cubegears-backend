"""Estimate skip is an audited workflow decision, not customer approval."""
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.roles.models import Role
from apps.companies.models import Company
from apps.branches.models import Branch
from apps.customers.models import Customer
from apps.vehicles.models import Vehicle
from apps.jobs.models import Job, JobEstimate, JobActivity


@override_settings(CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
                   CREDIT_LABEL_ONLY_ACCESS_ENABLED=False,
                   ESTIMATE_ZERO_GST_CLASSIFICATIONS={"000001": ["nil_rated"]})
class EstimateSkipTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Estimate Test Garage", slug="estimate-test-garage")
        self.branch = Branch.objects.create(company=self.company, name="Main", code="MAIN")
        role = Role.objects.create(company=self.company, name="Owner", code="EST_OWNER",
                                   permissions=["*"], is_active=True)
        self.owner = User.objects.create_user(
            email="est-owner@example.test", password="test-only-password",
            name="Owner", company=self.company, branch=self.branch, role=role,
        )
        self.company.owner = self.owner
        self.company.save(update_fields=["owner"])
        customer = Customer.objects.create(company=self.company, branch=self.branch,
                                           name="Test Customer", phone="+919900000001")
        vehicle = Vehicle.objects.create(company=self.company, branch=self.branch,
                                         customer=customer, registration="KL10AB8877")
        self.job = Job.objects.create(company=self.company, branch=self.branch,
                                     customer=customer, vehicle=vehicle,
                                     job_number="EST-001", status="Estimate Pending",
                                     inspection={"status": "Completed"},
                                     workflow_progress={"completed": ["overview", "inspection"]})
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)

    def url(self, suffix):
        return f"/api/v1/jobs/{self.job.pk}/{suffix}"

    def skip(self, reason="Quick Service", details=""):
        return self.client.post(self.url("estimate/skip"),
                                {"reason": reason, "details": details}, format="json")

    def test_skip_records_actor_and_advances_without_fake_quote(self):
        reply = self.skip()
        self.assertEqual(reply.status_code, 200, reply.data)
        self.assertEqual(reply.data["current"], "work")
        self.assertEqual(reply.data["estimateDecision"]["status"], "Skipped")
        self.assertEqual(reply.data["estimateDecision"]["skippedBy"], str(self.owner.pk))
        self.assertFalse(JobEstimate.objects.filter(job=self.job).exists())
        self.assertEqual(JobActivity.objects.filter(job=self.job, event="Estimate Skipped").count(), 1)
        self.assertEqual(self.skip().status_code, 400)

    def test_draft_is_preserved_and_other_reason_is_required(self):
        draft = JobEstimate.objects.create(company=self.company, branch=self.branch,
                                           job=self.job, version=1, status="Draft")
        self.assertEqual(self.skip("Other", "x").status_code, 400)
        self.assertEqual(self.skip("Other", "Not needed at present").status_code, 200)
        draft.refresh_from_db()
        self.assertEqual(draft.status, "Draft")

    def test_inspection_is_required(self):
        self.job.inspection = {"status": "In Progress"}
        self.job.save(update_fields=["inspection"])
        self.assertEqual(self.skip().status_code, 400)

    def test_view_only_role_denied(self):
        role = Role.objects.create(company=self.company, name="Reader", code="EST_READER",
                                   permissions=["jobs.view"], is_active=True)
        viewer = User.objects.create_user(email="est-reader@example.test",
                                          password="test-only-password", name="Reader",
                                          company=self.company, role=role)
        self.client.force_authenticate(user=viewer)
        self.assertEqual(self.skip().status_code, 403)

    def test_skip_is_separate_from_approval_in_workflow_and_audit(self):
        reply = self.skip()
        self.assertEqual(reply.status_code, 200, reply.data)
        self.assertEqual(reply.data["estimateDecision"]["status"], "Skipped")
        self.assertIn("estimate", reply.data["skipped"])
        self.job.refresh_from_db()
        self.assertIn("estimate", self.job.workflow_progress["skipped"])
        self.assertEqual(
            self.job.workflow_progress["skipDetails"]["estimate"]["reason"],
            "Quick Service",
        )
        self.assertEqual(
            self.client.get(self.url("workflow")).data["estimateDecision"]["status"],
            "Skipped",
        )
        self.assertFalse(self.job.estimates.filter(status="Approved").exists())
        self.assertEqual(JobActivity.objects.filter(
            job=self.job, event="Estimate Skipped",
        ).count(), 1)

    def test_workshop_policy_prevents_estimate_skip(self):
        self.company.require_customer_estimate_approval = True
        self.company.save(update_fields=["require_customer_estimate_approval"])
        reply = self.skip()
        self.assertEqual(reply.status_code, 400, reply.data)
        self.assertIn("policy", str(reply.data).lower())
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, "Estimate Pending")
        self.assertFalse(JobActivity.objects.filter(job=self.job, event="Estimate Skipped").exists())

    def test_mandatory_and_critical_inspection_findings_block_estimate_skip(self):
        for inspection in (
            {"status": "Completed", "requiresEstimateApproval": True},
            {"status": "Completed", "mandatoryEstimateApproval": True},
            {"status": "Completed", "checklist": {"Brake pads": "Critical"}},
            {"status": "Completed", "findings": [{"severity": "Critical", "description": "No brakes"}]},
        ):
            self.job.inspection = inspection
            self.job.save(update_fields=["inspection"])
            reply = self.skip()
            self.assertEqual(reply.status_code, 400, (inspection, reply.data))
            self.assertFalse(JobActivity.objects.filter(job=self.job, event="Estimate Skipped").exists())
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, "Estimate Pending")

    def gst_payload(self):
        self.company.gstin = "32ABCDE1234F1Z5"
        self.company.state = "Kerala"
        self.company.save(update_fields=["gstin", "state"])
        self.branch.state = "Kerala"
        self.branch.save(update_fields=["state"])
        return {
            "lines": [
                {"description": "Classified item", "type": "Service", "quantity": "2",
                 "unitPrice": "250", "taxRate": "0", "taxChoice": "none",
                 "taxTreatment": "nil_rated", "taxTreatmentConfirmed": True,
                 "hsnSac": "000001"},
                {"description": "Taxable work", "type": "Labour", "quantity": "1",
                 "unitPrice": "1000", "taxRate": "18", "taxChoice": "cgst:9",
                 "hsnSac": "000001"},
            ],
            "discount": "150", "taxPercent": "0",
            "gst": {"mode": "gst", "placeOfSupply": "32"},
        }

    def test_classified_zero_gst_and_mixed_tax_lines_persist(self):
        from decimal import Decimal
        payload = self.gst_payload()
        response = self.client.post(self.url("estimates/quick"), payload, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        estimate = self.job.estimates.get()
        self.assertEqual(estimate.subtotal, Decimal("1500.00"))
        self.assertEqual(estimate.tax, Decimal("162.00"))
        self.assertEqual(estimate.total, Decimal("1512.00"))
        self.assertEqual(estimate.items[0]["taxRate"], "0")
        self.assertEqual(estimate.items[0]["taxChoice"], "none")
        self.assertEqual(estimate.items[0]["taxTreatment"], "nil_rated")
        self.assertTrue(estimate.items[0]["taxTreatmentConfirmed"])
        self.assertEqual(estimate.items[0]["cgst"], "0.00")
        self.assertEqual(estimate.items[1]["cgst"], "81.00")
        self.assertEqual(estimate.items[-1]["_pricing"]["gst"]["sgst"], "81.00")
        payload["lines"][0]["description"] = "Updated classified item"
        response = self.client.patch(
            self.url(f"estimates/quick/{estimate.pk}"), payload, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)
        estimate.refresh_from_db()
        self.assertEqual(estimate.items[0]["description"], "Updated classified item")
        self.assertEqual(estimate.items[0]["taxTreatment"], "nil_rated")

    def test_zero_gst_requires_explicit_hsn_classification_and_confirmation(self):
        self.gst_payload()
        for invalid in [
            {"taxTreatment": ""}, {"taxTreatmentConfirmed": False},
            {"hsnSac": ""}, {"taxChoice": ""}, {"taxRate": "18"},
            {"taxChoice": "cgst:9"}, {"taxTreatment": "unsupported"},
        ]:
            payload = self.gst_payload()
            payload["lines"][0].update(invalid)
            response = self.client.post(
                self.url("estimates/quick"), payload, format="json"
            )
            self.assertEqual(response.status_code, 400, (invalid, response.data))
        self.assertFalse(self.job.estimates.exists())

    def test_interstate_gst_and_no_gst_have_distinct_totals(self):
        from decimal import Decimal
        payload = self.gst_payload()
        payload["gst"]["placeOfSupply"] = "33"
        payload["lines"][1]["taxChoice"] = "igst:18"
        response = self.client.post(self.url("estimates/quick"), payload, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        estimate = self.job.estimates.get()
        self.assertEqual(estimate.tax, Decimal("162.00"))
        self.assertEqual(estimate.items[0]["igst"], "0.00")
        self.assertEqual(estimate.items[1]["igst"], "162.00")

    def test_approved_estimate_cannot_be_skipped_or_edited(self):
        payload = self.gst_payload()
        response = self.client.post(self.url("estimates/quick"), payload, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        response = self.client.post(
            self.url("workflow"), {"stage": "estimate", "approveEstimate": True},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["estimateDecision"]["status"], "Approved")
        self.assertEqual(self.skip().status_code, 400)
        self.assertEqual(self.client.patch(
            self.url(f"estimates/quick/{self.job.estimates.get().pk}"),
            payload, format="json",
        ).status_code, 400)
