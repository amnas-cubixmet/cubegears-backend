"""Part 19: consent-based estimate notifications, manual drafts and reminders."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.jobs.models import Job, JobEstimate
from apps.roles.models import Role
from apps.vehicles.models import Vehicle
from apps.whatsapp.models import (
    WhatsAppBusinessConnection, WhatsAppMessageTemplate,
    WhatsAppNotificationLog, CustomerNotificationPreference,
)
from apps.whatsapp.tasks import (
    queue_customer_notification, remind_open_estimates,
    send_whatsapp_notification,
)


@override_settings(
    CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
    CREDIT_LABEL_ONLY_ACCESS_ENABLED=False,
    WHATSAPP_SEND_ENABLED=False,
)
class Part19EstimateNotificationTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Part19 Garage", slug="part19garage")
        self.branch = Branch.objects.create(
            company=self.company, name="Main", code="MAIN",
        )
        role = Role.objects.create(
            company=self.company, name="Owner", code="OWNER",
            permissions=["*"], is_active=True,
        )
        owner = User.objects.create_user(
            email="part19-owner@example.test", password="unit-test-only-password",
            name="Part19 Owner", company=self.company,
            branch=self.branch, role=role,
        )
        self.company.owner = owner
        self.company.save(update_fields=["owner"])
        self.customer = Customer.objects.create(
            company=self.company, branch=self.branch,
            name="Quote Driver", phone="+919876543210",
        )
        self.vehicle = Vehicle.objects.create(
            company=self.company, branch=self.branch, customer=self.customer,
            registration="KL10QQ1919",
        )
        self.job = Job.objects.create(
            company=self.company, branch=self.branch, customer=self.customer,
            vehicle=self.vehicle, job_number="PART19-001",
            status="Estimate Pending",
            inspection={"status": "Completed"},
            workflow_progress={"completed": ["overview", "inspection"]},
        )
        self.quote = JobEstimate.objects.create(
            company=self.company, branch=self.branch, job=self.job,
            version=1, status="Draft",
            subtotal=Decimal("1200.00"), total=Decimal("1200.00"),
            items=[{
                "description": "Brake inspection", "type": "Labour",
                "quantity": "1.00", "unitPrice": "1200.00", "total": "1200.00",
            }],
        )
        self.conn = WhatsAppBusinessConnection.objects.create(
            company=self.company, connected=True, notifications_enabled=True,
            messaging_mode="automatic", phone_number_id="111222333444",
            event_settings={
                "estimate_shared": True,
                "estimate_reminder": True,
                "estimate_reminder_hours": 24,
            },
        )
        self.preference = CustomerNotificationPreference.objects.create(
            company=self.company, branch=self.branch,
            customer=self.customer, whatsapp_opt_in=True,
            consent_source="Signed job intake sheet",
        )
        self.template = WhatsAppMessageTemplate.objects.create(
            company=self.company, branch=self.branch,
            event="estimate_shared", template_name="cubegears_estimate_review",
            approval_status="APPROVED",
        )
        WhatsAppMessageTemplate.objects.create(
            company=self.company, branch=self.branch,
            event="estimate_reminder", template_name="cubegears_estimate_reminder",
            approval_status="APPROVED",
        )
        self.staff = APIClient()
        self.staff.force_authenticate(user=owner)
        self.public = APIClient()

    def share(self):
        return self.staff.post(
            f"/api/v1/jobs/{self.job.pk}/estimates/{self.quote.pk}/share",
            {"action": "share"}, format="json",
        )

    def withdraw(self):
        return self.staff.post(
            f"/api/v1/jobs/{self.job.pk}/estimates/{self.quote.pk}/share",
            {"action": "withdraw"}, format="json",
        )

    def test_published_quote_queues_one_idempotent_notification_only(self):
        with self.captureOnCommitCallbacks(execute=True):
            result = self.share()
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data["status"], "Awaiting Customer")
        log = WhatsAppNotificationLog.objects.get(event="estimate_shared")
        self.assertEqual(log.idempotency_key, f"estimate:{self.quote.pk}:shared")
        self.assertEqual(log.status, "failed")
        self.assertNotIn("/access/", str(log.__dict__))
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.share().status_code, 200)
        self.assertEqual(
            WhatsAppNotificationLog.objects.filter(event="estimate_shared").count(), 1
        )

    def test_opt_out_and_manual_mode_never_queue_automatic_share(self):
        self.preference.whatsapp_opt_in = False
        self.preference.save(update_fields=["whatsapp_opt_in"])
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.share().status_code, 200)
        self.assertFalse(WhatsAppNotificationLog.objects.exists())
        self.assertEqual(self.withdraw().status_code, 200)
        self.preference.whatsapp_opt_in = True
        self.preference.save(update_fields=["whatsapp_opt_in"])
        self.conn.messaging_mode = "manual"
        self.conn.save(update_fields=["messaging_mode"])
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.share().status_code, 200)
        self.assertFalse(WhatsAppNotificationLog.objects.exists())

    def test_manual_estimate_draft_has_secure_link_but_not_sent_status(self):
        self.assertEqual(self.public.post(
            "/api/v1/whatsapp/manual-share", {
                "targetType": "estimate", "targetId": str(self.quote.pk)
            }, format="json",
        ).status_code, 403)
        unshared = self.staff.post("/api/v1/whatsapp/manual-share", {
            "targetType": "estimate", "targetId": str(self.quote.pk)
        }, format="json")
        self.assertEqual(unshared.status_code, 409)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.share().status_code, 200)
        drafted = self.staff.post("/api/v1/whatsapp/manual-share", {
            "targetType": "estimate", "targetId": str(self.quote.pk)
        }, format="json")
        self.assertEqual(drafted.status_code, 200, drafted.data)
        self.assertEqual(drafted.data["status"], "manual_prepared")
        self.assertIn("Estimate V1", drafted.data["preview"])
        self.assertIn("PART19-001", drafted.data["preview"])
        self.assertIn("1200.00", drafted.data["preview"])
        self.assertIn("https://wa.me/919876543210?", drafted.data["url"])
        self.assertIn("%2Faccess%2F", drafted.data["url"])
        self.assertFalse(WhatsAppNotificationLog.objects.filter(
            event="manual_estimate", status="sent",
        ).exists())
        self.assertEqual(self.withdraw().status_code, 200)
        self.assertEqual(self.staff.post("/api/v1/whatsapp/manual-share", {
            "targetType": "estimate", "targetId": str(self.quote.pk)
        }, format="json").status_code, 409)

    def test_rejected_quote_is_not_queued_or_sent(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.share().status_code, 200)
        self.assertEqual(self.withdraw().status_code, 200)
        self.assertIsNone(queue_customer_notification(
            self.customer, "estimate_shared",
            f"estimate:{self.quote.pk}:shared", job=self.job,
        ))
        self.assertIsNone(queue_customer_notification(
            self.customer, "estimate_reminder",
            f"estimate:{self.quote.pk}:reminder:1", job=self.job,
        ))

    @override_settings(
        WHATSAPP_SEND_ENABLED=True,
        WHATSAPP_ESTIMATE_REMINDERS_ENABLED=True,
        CELERY_BROKER_URL="redis://localhost:6379/0",
    )
    def test_daily_sweep_idempotently_queues_only_due_customer_quotes(self):
        with patch("apps.whatsapp.tasks.send_whatsapp_notification.delay"):
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(self.share().status_code, 200)
            updated = timezone.now() - timedelta(hours=50)
            JobEstimate.objects.filter(pk=self.quote.pk).update(updated_at=updated)
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(remind_open_estimates.run(), 1)
                self.assertEqual(remind_open_estimates.run(), 0)
            self.assertEqual(WhatsAppNotificationLog.objects.filter(
                event="estimate_reminder",
            ).count(), 1)
            # New slot is distinct; still one notification per cadence slot.
            JobEstimate.objects.filter(pk=self.quote.pk).update(
                updated_at=timezone.now() - timedelta(hours=75)
            )
            with self.captureOnCommitCallbacks(execute=True):
                remind_open_estimates.run()
            self.assertEqual(WhatsAppNotificationLog.objects.filter(
                event="estimate_reminder",
            ).count(), 2)

        self.conn.event_settings["estimate_reminder_hours"] = 48
        self.conn.save(update_fields=["event_settings"])
        self.assertEqual(self.withdraw().status_code, 200)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(remind_open_estimates.run(), 0)

    @override_settings(
        WHATSAPP_SEND_ENABLED=True,
        CELERY_BROKER_URL="redis://localhost:6379/0",
    )
    def test_worker_skips_withdrawn_estimate_even_if_message_was_prequeued(self):
        with patch("apps.whatsapp.tasks.send_whatsapp_notification.delay"):
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(self.share().status_code, 200)
        log = WhatsAppNotificationLog.objects.get(event="estimate_shared")
        self.assertEqual(log.status, "queued")
        self.assertEqual(self.withdraw().status_code, 200)
        with patch("apps.whatsapp.tasks.send_template_message") as send:
            self.assertEqual(send_whatsapp_notification.run(str(log.pk)), "skipped")
        send.assert_not_called()
        log.refresh_from_db()
        self.assertEqual(log.status, "skipped")

    def test_cross_tenant_and_invalid_reminder_cadence_are_blocked(self):
        other = Company.objects.create(name="Other Garage", slug="part19other")
        foreign = Customer.objects.create(
            company=other, name="Unrelated", phone="+919111111111"
        )
        self.assertEqual(self.staff.post("/api/v1/whatsapp/manual-share", {
            "targetType": "estimate", "targetId": str(self.quote.pk),
        }, format="json").status_code, 409)
        # A quote belonging to another workshop must never be exposed.
        self.assertIsNone(queue_customer_notification(
            foreign, "estimate_shared",
            f"estimate:{self.quote.pk}:shared", job=self.job,
        ))
        for invalid in (0, 12, 72, "24", True):
            response = self.staff.patch("/api/v1/whatsapp/settings", {
                "events": {
                    "estimate_reminder": True,
                    "estimate_reminder_hours": invalid,
                },
            }, format="json")
            self.assertEqual(response.status_code, 400)
        valid = self.staff.patch("/api/v1/whatsapp/settings", {
            "events": {"estimate_shared": True, "estimate_reminder": True,
                       "estimate_reminder_hours": 48},
        }, format="json")
        self.assertEqual(valid.status_code, 200, valid.data)
        self.assertEqual(valid.data["events"]["estimate_reminder_hours"], 48)
