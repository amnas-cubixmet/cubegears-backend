"""Manual-only WhatsApp sharing and historical-data regression coverage."""
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.roles.models import Role
from apps.whatsapp.models import (
    WhatsAppBusinessConnection, WhatsAppMessageTemplate,
    WhatsAppNotificationLog, CustomerNotificationPreference,
)
from apps.whatsapp.tasks import queue_customer_notification
from apps.whatsapp.phone import normalize_recipient


@override_settings(CREDIT_FEATURE_ENFORCEMENT_ENABLED=False, CREDIT_LABEL_ONLY_ACCESS_ENABLED=False)
class WhatsAppManualOnlyTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="WA Workshop", slug="whatsapp-tenant-test")
        role = Role.objects.create(company=self.company, name="Owner", code="WA_OWNER",
                                   permissions=["*"], is_active=True)
        owner = User.objects.create_user(email="wa-owner@example.test",
                                          password="safe-password", name="Workshop Owner",
                                          company=self.company, role=role)
        self.company.owner = owner
        self.company.save(update_fields=["owner"])
        self.customer = Customer.objects.create(company=self.company, name="Customer",
                                                 phone="+919888888888")
        self.client = APIClient()
        self.client.force_authenticate(user=owner)
        self.public = APIClient()

    def test_only_manual_mode_is_supported_and_no_meta_routes_exist(self):
        result = self.client.get("/api/v1/whatsapp/settings")
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data["messagingMode"], "manual")
        self.assertFalse(result.data["sendEnabled"])
        self.assertEqual(self.client.patch("/api/v1/whatsapp/settings",
            {"messagingMode": "automatic"}, format="json").status_code, 400)
        self.assertEqual(self.client.patch("/api/v1/whatsapp/settings",
            {"messagingMode": "manual"}, format="json").status_code, 200)
        for endpoint in ("connect", "disconnect", "templates", "test", "webhook"):
            self.assertEqual(self.client.post("/api/v1/whatsapp/" + endpoint,
                {}, format="json").status_code, 404)
        self.assertIn(self.public.get("/api/v1/whatsapp/settings").status_code, (401, 403))

    def test_old_cloud_configuration_cannot_trigger_automatic_delivery(self):
        WhatsAppBusinessConnection.objects.create(
            company=self.company, messaging_mode="automatic", connected=True,
            notifications_enabled=True, phone_number_id="historical",
            encrypted_access_token="encrypted-old-value",
            event_settings={"job_created": True},
        )
        WhatsAppMessageTemplate.objects.create(
            company=self.company, event="job_created",
            template_name="historical", approval_status="APPROVED",
        )
        CustomerNotificationPreference.objects.create(
            company=self.company, customer=self.customer,
            whatsapp_opt_in=True, consent_source="Signed intake form",
        )
        self.assertIsNone(queue_customer_notification(self.customer, "job_created", "legacy:job"))
        self.assertFalse(WhatsAppNotificationLog.objects.exists())

    def test_manual_share_prepares_draft_but_never_marks_delivery(self):
        result = self.client.post("/api/v1/whatsapp/manual-share", {
            "targetType": "customer", "targetId": str(self.customer.pk),
        }, format="json")
        self.assertEqual(result.status_code, 200, result.data)
        self.assertTrue(result.data["url"].startswith("https://wa.me/919888888888?text="))
        self.assertIn("%3A%2F%2F", result.data["url"])
        self.assertEqual(result.data["status"], "manual_prepared")
        self.assertEqual(result["Cache-Control"], "no-store")
        self.assertEqual(
            WhatsAppNotificationLog.objects.get(company=self.company).status,
            "manual_prepared",
        )

    def test_invalid_number_and_tenant_isolation(self):
        self.customer.phone = "9888888888"
        self.customer.save(update_fields=["phone"])
        invalid = self.client.post("/api/v1/whatsapp/manual-share", {
            "targetType": "customer", "targetId": str(self.customer.pk),
        }, format="json")
        self.assertEqual(invalid.status_code, 400)
        other = Company.objects.create(name="Other Workshop", slug="wa-other-tenant")
        foreign = Customer.objects.create(company=other, name="Other", phone="+919777777777")
        forbidden = self.client.post("/api/v1/whatsapp/manual-share", {
            "targetType": "customer", "targetId": str(foreign.pk),
        }, format="json")
        self.assertEqual(forbidden.status_code, 404)

    def test_phone_normalization_covers_supported_countries(self):
        self.assertEqual(normalize_recipient("+91 (98765) 43210"), "919876543210")
        self.assertEqual(normalize_recipient("+971 501234567"), "971501234567")
        self.assertEqual(normalize_recipient("+974 12345678"), "97412345678")
        self.assertEqual(normalize_recipient("9876543210"), "")
        self.assertEqual(normalize_recipient("+91987654"), "")
        self.assertEqual(normalize_recipient("+999123456789"), "")

    def test_historical_failed_log_can_be_shared_manually_without_mutation(self):
        history = WhatsAppNotificationLog.objects.create(
            company=self.company, customer=self.customer, event="job_created",
            idempotency_key="historical:failed", phone="919888888888", status="failed",
        )
        result = self.client.post("/api/v1/whatsapp/manual-share", {
            "targetType": "log", "targetId": str(history.pk),
        }, format="json")
        self.assertEqual(result.status_code, 200, result.data)
        history.refresh_from_db()
        self.assertEqual(history.status, "failed")
