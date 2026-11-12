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
    queue_customer_notification,
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

    def test_publishing_estimate_never_schedules_whatsapp_message(self):
        with self.captureOnCommitCallbacks(execute=True):
            result = self.share()
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data["status"], "Awaiting Customer")
        self.assertFalse(WhatsAppNotificationLog.objects.exists())
        self.assertIsNone(queue_customer_notification(
            self.customer, "estimate_shared", f"estimate:{self.quote.pk}:shared", job=self.job
        ))

    def test_latest_published_estimate_has_manual_secure_draft(self):
        missing = self.staff.post("/api/v1/whatsapp/manual-share", {
            "targetType": "estimate", "targetId": str(self.quote.pk)
        }, format="json")
        self.assertEqual(missing.status_code, 409)
        self.assertEqual(self.share().status_code, 200)
        drafted = self.staff.post("/api/v1/whatsapp/manual-share", {
            "targetType": "estimate", "targetId": str(self.quote.pk)
        }, format="json")
        self.assertEqual(drafted.status_code, 200, drafted.data)
        self.assertIn("Estimate V1", drafted.data["preview"])
        self.assertIn("wa.me/", drafted.data["url"])
        self.assertEqual(drafted.data["status"], "manual_prepared")
        self.assertFalse(WhatsAppNotificationLog.objects.filter(status="sent").exists())

    def test_withdrawn_estimate_cannot_be_shared_or_auto_reminded(self):
        self.assertEqual(self.share().status_code, 200)
        self.assertEqual(self.withdraw().status_code, 200)
        self.assertIsNone(queue_customer_notification(
            self.customer, "estimate_reminder",
            f"estimate:{self.quote.pk}:reminder:1", job=self.job
        ))
        result = self.staff.post("/api/v1/whatsapp/manual-share", {
            "targetType": "estimate", "targetId": str(self.quote.pk)
        }, format="json")
        self.assertEqual(result.status_code, 409)
