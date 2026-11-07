"""Part 17: published estimate review, customer intent, versioning and security."""
import hashlib
import hmac
import os
import time
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.customers.portal_tokens import ensure_portal_access, issue_link, rotate_link
from apps.jobs.models import Job, JobActivity, JobEstimate
from apps.roles.models import Role
from apps.vehicles.models import Vehicle


@override_settings(
    CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
    CREDIT_LABEL_ONLY_ACCESS_ENABLED=False,
    CUSTOMER_PORTAL_BASE_URL="https://customer.cubegears.com/access",
)
class Part17CustomerEstimateTests(TestCase):
    def setUp(self):
        self.secret = "part17-integration-secret-over-32-characters-long"
        patcher = patch.dict(os.environ, {
            "PORTAL_PROXY_SHARED_SECRET": self.secret,
            "CUSTOMER_PORTAL_ROOT_DOMAIN": "cubegears.com",
        })
        patcher.start()
        self.addCleanup(patcher.stop)
        self.company = Company.objects.create(
            name="Part17 Workshop", slug="part17garage",
            require_customer_estimate_approval=True,
        )
        self.other = Company.objects.create(name="Another Workshop", slug="differentgarage")
        branch = Branch.objects.create(company=self.company, name="Main", code="MAIN")
        role = Role.objects.create(
            company=self.company, name="Owner", code="OWNER", permissions=["*"], is_active=True,
        )
        owner = User.objects.create_user(
            email="part17-owner@example.test", password="test-only-password",
            name="Part17 Owner", company=self.company, branch=branch, role=role,
        )
        self.company.owner = owner
        self.company.save(update_fields=["owner"])
        self.customer = Customer.objects.create(
            company=self.company, branch=branch, name="Portal Driver", phone="+919999999999",
        )
        self.unrelated = Customer.objects.create(
            company=self.company, branch=branch, name="Different Driver", phone="+919888888888",
        )
        vehicle = Vehicle.objects.create(
            company=self.company, branch=branch, customer=self.customer,
            registration="KL10AB1000",
        )
        other_vehicle = Vehicle.objects.create(
            company=self.company, branch=branch, customer=self.unrelated,
            registration="KL10AB2000",
        )
        self.job = Job.objects.create(
            company=self.company, branch=branch, customer=self.customer,
            vehicle=vehicle, job_number="PART17-001", status="Estimate Pending",
            inspection={"status": "Completed"},
            workflow_progress={"completed": ["overview", "inspection"]},
        )
        self.other_job = Job.objects.create(
            company=self.company, branch=branch, customer=self.unrelated,
            vehicle=other_vehicle, job_number="PART17-002", status="Estimate Pending",
            inspection={"status": "Completed"},
        )
        self.quote = JobEstimate.objects.create(
            company=self.company, branch=branch, job=self.job,
            version=1, status="Draft", subtotal=Decimal("1200.00"),
            total=Decimal("1200.00"),
            items=[{"description": "Brake repair", "type": "Labour",
                    "quantity": "1", "unitPrice": "1200", "total": "1200"}],
        )
        self.other_quote = JobEstimate.objects.create(
            company=self.company, branch=branch, job=self.other_job,
            version=1, status="Awaiting Customer", total=Decimal("999.00"),
        )
        self.staff = APIClient()
        self.staff.force_authenticate(owner)
        self.browser = APIClient()

    def headers(self, host="part17garage.cubegears.com"):
        ts = str(int(time.time()))
        digest = hmac.new(self.secret.encode(), (ts + ":" + host).encode(), hashlib.sha256).hexdigest()
        return {
            "HTTP_X_CG_PORTAL_HOST": host,
            "HTTP_X_CG_PORTAL_TS": ts,
            "HTTP_X_CG_PORTAL_SIG": digest,
        }

    def login(self, customer=None):
        customer = customer or self.customer
        self.assertEqual(self.browser.get(
            "/api/v1/customer-website/branding", **self.headers(),
        ).status_code, 200)
        token = issue_link(ensure_portal_access(customer)).rsplit("/", 1)[-1]
        result = self.browser.post(
            "/api/v1/customer-website/session", {"token": token}, format="json",
            HTTP_X_CG_CSRF=self.browser.cookies["cg_portal_csrf"].value,
            **self.headers(),
        )
        self.assertEqual(result.status_code, 200, result.data)

    def share(self, quote=None, action="share"):
        quote = quote or self.quote
        return self.staff.post(
            f"/api/v1/jobs/{self.job.pk}/estimates/{quote.pk}/share",
            {"action": action}, format="json",
        )

    def decide(self, decision, quote=None, version=1, csrf=True):
        quote = quote or self.quote
        extras = ({
            "HTTP_X_CG_CSRF": self.browser.cookies["cg_portal_csrf"].value,
        } if csrf else {})
        dashboard = self.browser.get(
            "/api/v1/customer-website/session", **self.headers(),
        )
        quotes = dashboard.data.get("estimates", []) if dashboard.status_code == 200 else []
        review_hash = next(
            (q["reviewHash"] for q in quotes if q["id"] == str(quote.pk)), None
        )
        return self.browser.post(
            f"/api/v1/customer-website/estimates/{quote.pk}/decision",
            {"decision": decision, "version": version, "reviewHash": review_hash},
            format="json", **extras, **self.headers(),
        )

    def test_only_staff_published_quotes_are_visible(self):
        self.login()
        before = self.browser.get("/api/v1/customer-website/session", **self.headers())
        self.assertEqual(before.status_code, 200)
        self.assertEqual(before.data["estimates"], [])
        self.assertEqual(self.decide("accept").status_code, 409)
        shared = self.share()
        self.assertEqual(shared.status_code, 200, shared.data)
        self.assertEqual(shared.data["status"], "Awaiting Customer")
        after = self.browser.get("/api/v1/customer-website/session", **self.headers())
        self.assertEqual(len(after.data["estimates"]), 1)
        self.assertEqual(after.data["estimates"][0]["id"], str(self.quote.pk))
        self.assertEqual(after.data["estimates"][0]["total"], "1200.00")
        self.assertNotIn(str(self.other_quote.pk), str(after.data))
        self.assertEqual(after.data["estimates"][0]["lines"][0]["description"], "Brake repair")
        self.assertTrue(after.data["estimates"][0]["reviewable"])
        self.assertEqual(len(after.data["estimates"][0]["reviewHash"]), 64)
        self.assertNotIn("_pricing", str(after.data))
        self.assertNotIn("catalogId", str(after.data))

    def test_customer_acceptance_is_idempotent_audited_and_does_not_start_work(self):
        self.login()
        self.assertEqual(self.share().status_code, 200)
        response = self.decide("accept")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "Customer Approved")
        self.assertEqual(self.decide("accept").status_code, 200)
        self.quote.refresh_from_db()
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, "Estimate Pending")
        self.assertEqual(self.quote.status, "Customer Approved")
        self.assertEqual(self.job.workflow_progress["customerEstimateDecision"]["version"], 1)
        self.assertEqual(self.job.workflow_progress["customerEstimateDecision"]["confirmationSource"], "customer_portal_session")
        self.assertEqual(JobActivity.objects.filter(
            job=self.job, event="Customer Estimate Accepted",
        ).count(), 1)

    def test_policy_blocks_staff_attestation_until_portal_acceptance(self):
        self.login()
        staff_approve = lambda: self.staff.post(
            f"/api/v1/jobs/{self.job.pk}/workflow",
            {"stage": "estimate", "approveEstimate": True,
             "estimateId": str(self.quote.pk), "estimateVersion": 1},
            format="json",
        )
        self.assertEqual(staff_approve().status_code, 400)
        self.assertEqual(self.share().status_code, 200)
        self.assertEqual(staff_approve().status_code, 400)
        self.assertEqual(self.decide("accept").status_code, 200)
        done = staff_approve()
        self.assertEqual(done.status_code, 200, done.data)
        self.quote.refresh_from_db()
        self.job.refresh_from_db()
        self.assertEqual(self.quote.status, "Approved")
        self.assertEqual(self.job.status, "In Progress")
        self.assertEqual(
            self.job.workflow_progress["estimateDecision"]["confirmationSource"],
            "customer_portal_session",
        )

    def test_decline_then_revised_version_required(self):
        self.login()
        self.assertEqual(self.share().status_code, 200)
        self.assertEqual(self.decide("decline").status_code, 200)
        self.assertEqual(self.decide("decline").status_code, 200)
        self.assertEqual(self.decide("accept").status_code, 409)
        self.assertEqual(JobActivity.objects.filter(
            job=self.job, event="Customer Estimate Declined",
        ).count(), 1)
        staff = self.staff.post(
            f"/api/v1/jobs/{self.job.pk}/workflow",
            {"stage": "estimate", "approveEstimate": True}, format="json",
        )
        self.assertEqual(staff.status_code, 400)
        retry = self.staff.post(
            f"/api/v1/jobs/{self.job.pk}/estimates/quick",
            {"lines": [{"type": "Labour", "description": "Revised repair",
                        "quantity": "1", "unitPrice": "1000"}],
             "taxPercent": "0", "discount": "0"},
            format="json",
        )
        self.assertEqual(retry.status_code, 201, retry.data)
        self.assertEqual(retry.data["version"], 2)

    def test_share_can_be_withdrawn_but_accepted_quote_cannot(self):
        self.login()
        self.assertEqual(self.share().status_code, 200)
        self.assertEqual(self.share().status_code, 200)
        self.assertEqual(JobActivity.objects.filter(
            job=self.job, event="Estimate Shared With Customer",
        ).count(), 1)
        self.assertEqual(self.share(action="withdraw").status_code, 200)
        self.assertEqual(self.decide("accept").status_code, 409)
        self.assertEqual(self.share().status_code, 200)
        self.assertEqual(self.decide("accept").status_code, 200)
        self.assertEqual(self.share(action="withdraw").status_code, 400)

    def test_csrf_invalid_version_cross_customer_and_rotated_link_blocked(self):
        self.login()
        self.assertEqual(self.share().status_code, 200)
        self.assertEqual(self.decide("accept", csrf=False).status_code, 403)
        self.assertEqual(self.decide("accept", version=99).status_code, 409)
        self.assertEqual(self.decide("accept", quote=self.other_quote).status_code, 404)
        rotate_link(ensure_portal_access(self.customer))
        self.assertEqual(self.decide("accept").status_code, 401)
        self.quote.refresh_from_db()
        self.assertEqual(self.quote.status, "Awaiting Customer")

    def test_stale_old_version_cannot_be_shared(self):
        newer = JobEstimate.objects.create(
            company=self.company, branch=self.job.branch, job=self.job,
            version=2, status="Draft", total=Decimal("1600.00"),
        )
        self.assertEqual(self.share().status_code, 400)
        self.assertEqual(self.staff.post(
            f"/api/v1/jobs/{self.job.pk}/estimates/{newer.pk}/share",
            {"action": "share"}, format="json",
        ).status_code, 200)

    def test_part18_exact_quote_review_hash_and_audited_receipt(self):
        self.login()
        self.assertEqual(self.share().status_code, 200)
        url = f"/api/v1/customer-website/estimates/{self.quote.pk}/decision"
        bad = self.browser.post(
            url, {"decision": "accept", "version": 1, "reviewHash": "0" * 64},
            format="json",
            HTTP_X_CG_CSRF=self.browser.cookies["cg_portal_csrf"].value,
            **self.headers(),
        )
        self.assertEqual(bad.status_code, 409)
        self.assertEqual(self.job.activities.filter(event="Customer Estimate Accepted").count(), 0)

        accepted = self.decide("accept")
        self.assertEqual(accepted.status_code, 200, accepted.data)
        receipt = accepted.data["decisionReceipt"]
        self.assertEqual(receipt["status"], "Accepted")
        self.assertTrue(receipt["id"])
        repeat = self.decide("accept")
        self.assertEqual(repeat.status_code, 200, repeat.data)
        self.assertEqual(repeat.data["decisionReceipt"]["id"], receipt["id"])
        self.assertEqual(self.job.activities.filter(event="Customer Estimate Accepted").count(), 1)
        self.job.refresh_from_db()
        self.assertEqual(self.job.workflow_progress["customerEstimateDecision"]["receiptId"], receipt["id"])
        self.assertEqual(
            str(self.job.activities.get(event="Customer Estimate Accepted").pk),
            receipt["id"],
        )
        dashboard = self.browser.get("/api/v1/customer-website/session", **self.headers())
        self.assertEqual(dashboard.data["estimates"][0]["decisionReceipt"]["id"], receipt["id"])

    def test_part18_quote_breakdown_is_whitelisted_and_reconciles_gst(self):
        self.quote.items = [
            {"description": "Brake pad", "type": "Part", "quantity": "2",
             "unitPrice": "500.00", "total": "1000.00", "hsnSac": "8708",
             "taxRate": "18", "catalogId": "private-catalog-id",
             "mechanicCost": "300.00"},
            {"_pricing": {"discount": "100.00", "gst": {
                "mode": "gst", "cgst": "81.00", "sgst": "81.00", "igst": "0.00",
                "requestFingerprint": "private-fingerprint",
            }, "requestId": "private-request-id"}},
        ]
        self.quote.subtotal = Decimal("1000.00")
        self.quote.tax = Decimal("162.00")
        self.quote.total = Decimal("1062.00")
        self.quote.save(update_fields=["items", "subtotal", "tax", "total"])
        self.login()
        self.assertEqual(self.share().status_code, 200)
        response = self.browser.get("/api/v1/customer-website/session", **self.headers())
        quote = response.data["estimates"][0]
        self.assertEqual(quote["subtotal"], "1000.00")
        self.assertEqual(quote["discount"], "100.00")
        self.assertEqual(quote["tax"], "162.00")
        self.assertEqual(quote["total"], "1062.00")
        self.assertEqual(quote["gst"], {
            "cgst": "81.00", "sgst": "81.00", "igst": "0.00",
        })
        self.assertEqual(quote["lines"][0]["quantity"], "2.00")
        self.assertEqual(quote["lines"][0]["hsnSac"], "8708")
        for secret in ("private-catalog-id", "mechanicCost", "requestId",
                       "private-request-id", "private-fingerprint", "_pricing"):
            self.assertNotIn(secret, str(quote))
        self.assertEqual(self.decide("accept").status_code, 200)

    def test_part18_inconsistent_or_empty_itemization_cannot_be_published(self):
        self.quote.subtotal = Decimal("1199.00")
        self.quote.save(update_fields=["subtotal"])
        self.assertEqual(self.share().status_code, 400)
        self.quote.subtotal = Decimal("1200.00")
        self.quote.items = [{"_pricing": {"requestId": "private"}}]
        self.quote.save(update_fields=["subtotal", "items"])
        self.assertEqual(self.share().status_code, 400)
        self.quote.items = [{"description": "Valid repair", "quantity": "1",
                             "unitPrice": "1200", "total": "1200"}]
        self.quote.save(update_fields=["items"])
        self.assertEqual(self.share().status_code, 200)

