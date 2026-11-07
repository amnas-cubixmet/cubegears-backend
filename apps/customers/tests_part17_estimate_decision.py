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
from apps.notifications.models import Notification
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

    def decide(self, decision, quote=None, version=1, csrf=True, decline_reason="price", decline_note=""):
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
            {"decision": decision, "version": version, "reviewHash": review_hash,
             **({"declineReason": decline_reason, "declineNote": decline_note}
                if decision == "decline" else {})},
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
            version=2, status="Draft", subtotal=Decimal("1600.00"),
            total=Decimal("1600.00"),
            items=[{"description": "Revised brake service", "type": "Labour",
                    "quantity": "1", "unitPrice": "1600.00", "total": "1600.00"}],
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

    def test_part19_owner_gets_one_private_actionable_notification_per_decision(self):
        self.login()
        self.assertEqual(self.share().status_code, 200)
        first = self.decide("accept")
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(self.decide("accept").status_code, 200)
        notices = Notification.objects.filter(
            company=self.company, notification_type="customer_estimate_accepted",
        )
        self.assertEqual(notices.count(), 1)
        notice = notices.get()
        self.assertEqual(notice.user_id, self.company.owner_id)
        self.assertEqual(notice.branch_id, self.job.branch_id)
        self.assertEqual(notice.data["jobId"], str(self.job.pk))
        self.assertEqual(notice.data["estimateId"], str(self.quote.pk))
        self.assertEqual(notice.data["receiptId"], first.data["decisionReceipt"]["id"])
        self.assertEqual(notice.data["route"], f"/jobs/{self.job.pk}/estimate")
        self.assertFalse(notice.is_read)
        self.assertNotIn("919999999999", str(notice.data))

        visible = self.staff.get("/api/v1/notifications")
        self.assertEqual(visible.status_code, 200, visible.data)
        rows = visible.data if isinstance(visible.data, list) else visible.data["results"]
        self.assertEqual(sum(row["id"] == str(notice.pk) for row in rows), 1)

    def test_part19_recipients_are_branch_and_role_scoped_and_reads_are_private(self):
        other_branch = Branch.objects.create(
            company=self.company, name="Side Branch", code="SIDE",
        )
        full_role = Role.objects.create(
            company=self.company, name="Estimator", code="ESTIMATOR",
            permissions=["jobs.view", "notifications.view", "notifications.edit"],
        )
        restricted_role = Role.objects.create(
            company=self.company, name="Only Jobs", code="JOBS_ONLY",
            permissions=["jobs.view"],
        )
        eligible = User.objects.create_user(
            email="part19-estimator@example.test", password="test-only-password",
            name="Estimator", company=self.company, branch=self.job.branch,
            role=full_role,
        )
        User.objects.create_user(
            email="part19-other-branch@example.test", password="test-only-password",
            name="Other Branch", company=self.company, branch=other_branch,
            role=full_role,
        )
        User.objects.create_user(
            email="part19-no-notify@example.test", password="test-only-password",
            name="Jobs Only", company=self.company, branch=self.job.branch,
            role=restricted_role,
        )
        User.objects.create_user(
            email="part19-inactive@example.test", password="test-only-password",
            name="Inactive", company=self.company, branch=self.job.branch,
            role=full_role, is_active=False,
        )
        self.login()
        self.assertEqual(self.share().status_code, 200)
        self.assertEqual(self.decide("decline").status_code, 200)
        notices = Notification.objects.filter(
            company=self.company, notification_type="customer_estimate_declined",
        )
        self.assertEqual(notices.count(), 2)
        self.assertEqual(
            set(notices.values_list("user_id", flat=True)),
            {self.company.owner_id, eligible.pk},
        )
        self.assertEqual(notices.filter(company=self.other).count(), 0)
        owner_notice = notices.get(user=self.company.owner)
        staff_notice = notices.get(user=eligible)
        other_client = APIClient()
        other_client.force_authenticate(eligible)
        self.assertEqual(self.staff.patch(
            f"/api/v1/notifications/{staff_notice.pk}/read", {}, format="json",
        ).status_code, 404)
        self.assertEqual(other_client.patch(
            f"/api/v1/notifications/{owner_notice.pk}/read", {}, format="json",
        ).status_code, 404)
        self.assertEqual(other_client.patch(
            f"/api/v1/notifications/{staff_notice.pk}/read", {}, format="json",
        ).status_code, 200)
        staff_notice.refresh_from_db()
        owner_notice.refresh_from_db()
        self.assertTrue(staff_notice.is_read)
        self.assertIsNotNone(staff_notice.read_at)
        self.assertFalse(owner_notice.is_read)
        self.assertIsNone(owner_notice.read_at)

    def test_part19_customer_estimate_alerts_cannot_be_forged_or_rewritten(self):
        self.login()
        self.assertEqual(self.share().status_code, 200)
        notice_before = Notification.objects.count()
        self.assertEqual(self.decide("accept").status_code, 200)
        notice = Notification.objects.get(
            company=self.company, notification_type="customer_estimate_accepted",
        )
        forged = self.staff.post(
            "/api/v1/notifications",
            {"title": "Fake accepted", "message": "Fake customer response",
             "notification_type": "customer_estimate_accepted"},
            format="json",
        )
        self.assertEqual(forged.status_code, 400)
        tampered = self.staff.patch(
            f"/api/v1/notifications/{notice.pk}",
            {"title": "Forged new amount", "data": {"route": "/jobs/fake/estimate"}},
            format="json",
        )
        self.assertEqual(tampered.status_code, 400)
        self.assertEqual(Notification.objects.count(), notice_before + 1)
        notice.refresh_from_db()
        self.assertNotEqual(notice.title, "Forged new amount")

    def test_part19_invalid_customer_decisions_do_not_create_inbox_alerts(self):
        self.login()
        self.assertEqual(self.share().status_code, 200)
        self.assertEqual(self.decide("accept", version=99).status_code, 409)
        self.assertEqual(self.decide("accept", csrf=False).status_code, 403)
        self.assertEqual(Notification.objects.filter(
            company=self.company,
            notification_type__startswith="customer_estimate_",
        ).count(), 0)

    def test_part20_decline_reason_and_note_audited_without_leaking_to_inbox(self):
        from apps.notifications.models import Notification
        self.login()
        self.assertEqual(self.share().status_code, 200)
        response = self.decide(
            "decline", decline_reason="items",
            decline_note="Please remove the additional brake service.",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["decisionReceipt"]["declineFeedback"], {
            "reason": "items", "note": "Please remove the additional brake service.",
        })
        activity = JobActivity.objects.get(job=self.job, event="Customer Estimate Declined")
        self.assertEqual(activity.metadata["declineReason"], "items")
        self.assertEqual(activity.metadata["declineNote"], "Please remove the additional brake service.")
        self.job.refresh_from_db()
        record = self.job.workflow_progress["customerEstimateDecision"]
        self.assertEqual(record["declineReason"], "items")
        self.assertEqual(record["declineNote"], "Please remove the additional brake service.")
        notice = Notification.objects.get(
            company=self.company, notification_type="customer_estimate_declined"
        )
        self.assertEqual(notice.data["declineReason"], "items")
        self.assertNotIn("Please remove", str(notice.data))
        dashboard = self.browser.get("/api/v1/customer-website/session", **self.headers())
        self.assertEqual(dashboard.status_code, 200)
        self.assertEqual(
            dashboard.data["estimates"][0]["decisionReceipt"]["declineFeedback"],
            {"reason": "items", "note": "Please remove the additional brake service."},
        )

    def test_part20_revised_quote_retains_old_decision_receipt(self):
        self.login()
        self.assertEqual(self.share().status_code, 200)
        declined = self.decide(
            "decline", decline_reason="price",
            decline_note="Can you offer a more affordable repair?",
        )
        self.assertEqual(declined.status_code, 200, declined.data)
        old_receipt = declined.data["decisionReceipt"]["id"]
        revision = self.staff.post(
            f"/api/v1/jobs/{self.job.pk}/estimates/quick",
            {"lines": [{"type": "Labour", "description": "Affordable brake repair",
                        "quantity": "1", "unitPrice": "950"}],
             "taxPercent": "0", "discount": "0"},
            format="json",
        )
        self.assertEqual(revision.status_code, 201, revision.data)
        self.assertEqual(revision.data["version"], 2)
        shared = self.staff.post(
            f"/api/v1/jobs/{self.job.pk}/estimates/{revision.data['id']}/share",
            {"action": "share"}, format="json",
        )
        self.assertEqual(shared.status_code, 200, shared.data)
        dashboard = self.browser.get("/api/v1/customer-website/session", **self.headers())
        self.assertEqual(dashboard.status_code, 200)
        estimates = {e["version"]: e for e in dashboard.data["estimates"]}
        self.assertEqual(set(estimates), {1, 2})
        self.assertEqual(estimates[1]["status"], "Customer Rejected")
        self.assertEqual(estimates[1]["decisionReceipt"]["id"], old_receipt)
        self.assertEqual(estimates[1]["decisionReceipt"]["declineFeedback"]["reason"], "price")
        self.assertEqual(estimates[2]["status"], "Awaiting Customer")
        self.assertNotIn("decisionReceipt", estimates[2])
        latest_hash = estimates[2]["reviewHash"]
        accepted = self.browser.post(
            f"/api/v1/customer-website/estimates/{revision.data['id']}/decision",
            {"decision": "accept", "version": 2, "reviewHash": latest_hash},
            format="json",
            HTTP_X_CG_CSRF=self.browser.cookies["cg_portal_csrf"].value,
            **self.headers(),
        )
        self.assertEqual(accepted.status_code, 200, accepted.data)
        refreshed = self.browser.get("/api/v1/customer-website/session", **self.headers())
        refreshed_by_version = {e["version"]: e for e in refreshed.data["estimates"]}
        self.assertEqual(refreshed_by_version[1]["decisionReceipt"]["id"], old_receipt)
        self.assertEqual(refreshed_by_version[2]["decisionReceipt"]["status"], "Accepted")

    def test_part20_invalid_feedback_and_replay_cannot_mutate_decision(self):
        from apps.notifications.models import Notification
        self.login()
        self.assertEqual(self.share().status_code, 200)
        path = f"/api/v1/customer-website/estimates/{self.quote.pk}/decision"
        quote = self.browser.get("/api/v1/customer-website/session", **self.headers()).data["estimates"][0]
        base = {"decision": "decline", "version": 1, "reviewHash": quote["reviewHash"]}
        def send(payload):
            return self.browser.post(
                path, payload, format="json",
                HTTP_X_CG_CSRF=self.browser.cookies["cg_portal_csrf"].value,
                **self.headers(),
            )
        self.assertEqual(send({**base, "declineReason": "not-an-option"}).status_code, 400)
        self.assertEqual(send({**base, "declineReason": "price", "declineNote": "x" * 501}).status_code, 400)
        self.assertEqual(send({**base, "declineReason": "price", "declineNote": 123}).status_code, 400)
        self.assertEqual(send({
            **base, "decision": "accept", "declineReason": "price"
        }).status_code, 400)
        self.assertEqual(JobActivity.objects.filter(job=self.job, event="Customer Estimate Declined").count(), 0)
        accepted = send({**base, "declineReason": "price", "declineNote": "Too expensive"})
        self.assertEqual(accepted.status_code, 200, accepted.data)
        duplicate = send({**base, "declineReason": "price", "declineNote": "Too expensive"})
        self.assertEqual(duplicate.status_code, 200, duplicate.data)
        conflicting = send({**base, "declineReason": "items", "declineNote": "Different request"})
        self.assertEqual(conflicting.status_code, 409, conflicting.data)
        self.assertEqual(JobActivity.objects.filter(job=self.job, event="Customer Estimate Declined").count(), 1)
        self.assertEqual(Notification.objects.filter(
            company=self.company, notification_type="customer_estimate_declined",
        ).count(), 1)

