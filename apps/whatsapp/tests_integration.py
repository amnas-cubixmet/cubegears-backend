"""Official WhatsApp integration must be consent-gated and inert without configuration."""
import hashlib
import hmac
import json
from unittest.mock import patch
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.roles.models import Role
from apps.whatsapp.models import (
    WhatsAppBusinessConnection, CustomerNotificationPreference,
    WhatsAppMessageTemplate, WhatsAppNotificationLog,
)
from apps.whatsapp.tasks import queue_customer_notification


@override_settings(
    CREDIT_FEATURE_ENFORCEMENT_ENABLED=False, CREDIT_LABEL_ONLY_ACCESS_ENABLED=False,
    WHATSAPP_SEND_ENABLED=False, WHATSAPP_META_APP_ID="",
    WHATSAPP_META_APP_SECRET="test-webhook-secret",
    WHATSAPP_META_CONFIG_ID="", WHATSAPP_WEBHOOK_VERIFY_TOKEN="webhook-test-token",
)
class WhatsAppSecurityTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="WA Workshop", slug="whatsapp-tenant-test")
        role = Role.objects.create(company=self.company,name="Owner",code="WA_OWNER",
                                   permissions=["settings.manage","customers.edit"],is_active=True)
        owner = User.objects.create_user(
            email="wa-owner@example.test",password="safe-password",
            name="WhatsApp Owner",company=self.company,role=role,
        )
        self.company.owner = owner
        self.company.save(update_fields=["owner"])
        self.customer = Customer.objects.create(company=self.company,name="Customer",phone="+919888888888")
        self.client = APIClient()
        self.client.force_authenticate(user=owner)
        self.public = APIClient()

    def test_settings_show_disabled_without_meta_configuration(self):
        data = self.client.get("/api/v1/whatsapp/settings")
        self.assertEqual(data.status_code, 200, data.data)
        self.assertFalse(data.data["configured"])
        self.assertFalse(data.data["connected"])
        invalid = self.client.post("/api/v1/whatsapp/connect", {
            "code":"invalid", "wabaId":"1234567890", "phoneNumberId":"1234567890",
        }, format="json")
        self.assertEqual(invalid.status_code, 503)
        self.assertIn(self.public.get("/api/v1/whatsapp/settings").status_code, [401,403])

    def test_queue_rejects_without_connection_consent_and_approved_template(self):
        self.assertIsNone(queue_customer_notification(self.customer,"job_created","job:a:created"))
        conn = WhatsAppBusinessConnection.objects.create(
            company=self.company, connected=True, notifications_enabled=True, messaging_mode="automatic",
            phone_number_id="123456789",event_settings={"job_created":True},
        )
        self.assertIsNone(queue_customer_notification(self.customer,"job_created","job:a:created"))
        consent = self.client.post(
            f"/api/v1/whatsapp/customers/{self.customer.id}/consent",
            {"optIn":True, "source":"Signed consent on service intake form"},format="json",
        )
        self.assertEqual(consent.status_code, 200, consent.data)
        self.assertTrue(CustomerNotificationPreference.objects.get(customer=self.customer).whatsapp_opt_in)
        missing_template = queue_customer_notification(self.customer,"job_created","job:a:created")
        self.assertEqual(missing_template.status, "failed")
        self.assertIn("template", missing_template.error_detail)
        WhatsAppMessageTemplate.objects.create(
            company=self.company,event="job_created",template_name="approved_job",approval_status="APPROVED",
        )
        first = queue_customer_notification(self.customer,"job_created","job:a:created")
        second = queue_customer_notification(self.customer,"job_created","job:a:created")
        self.assertIsNotNone(first)
        self.assertEqual(first.id, second.id)
        self.assertEqual(WhatsAppNotificationLog.objects.filter(company=self.company).count(),1)
        self.assertEqual(first.status,"failed")
        conn.connected = False
        conn.save(update_fields=["connected"])
        self.assertEqual(queue_customer_notification(self.customer,"job_created","job:b:created").status, "failed")

    def test_customer_opt_out_is_recorded_and_prevents_new_notifications(self):
        consent = self.client.post(
            f"/api/v1/whatsapp/customers/{self.customer.id}/consent",
            {"optIn":True, "source":"Recorded signed consent"},format="json",
        )
        self.assertEqual(consent.status_code, 200)
        opt_out = self.client.post(
            f"/api/v1/whatsapp/customers/{self.customer.id}/consent",
            {"optIn":False},format="json",
        )
        self.assertEqual(opt_out.status_code, 200)
        self.assertFalse(CustomerNotificationPreference.objects.get(customer=self.customer).whatsapp_opt_in)

    def test_webhook_rejects_unsigned_and_accepts_signed_without_sending(self):
        self.assertEqual(self.public.post(
            "/api/v1/whatsapp/webhook",data={"entry":[]},format="json",
        ).status_code,403)
        verification = self.public.get("/api/v1/whatsapp/webhook", {
            "hub.mode":"subscribe", "hub.verify_token":"webhook-test-token",
            "hub.challenge":"verified-123",
        })
        self.assertEqual(verification.status_code,200)
        self.assertEqual(verification.content,b"verified-123")
        data = json.dumps({"object":"whatsapp_business_account","entry":[]}).encode()
        sig = hmac.new(b"test-webhook-secret",data,hashlib.sha256).hexdigest()
        reply = self.public.generic(
            "POST","/api/v1/whatsapp/webhook", data=data,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256="sha256="+sig,
        )
        self.assertEqual(reply.status_code,200)

    def test_manual_mode_can_be_saved_without_meta_and_prepares_private_tracking_link(self):
        saved = self.client.patch("/api/v1/whatsapp/settings", {
            "messagingMode": "manual",
            "events": {"job_created": True},
            "notificationsEnabled": False,
        }, format="json")
        self.assertEqual(saved.status_code, 200, saved.data)
        self.assertEqual(saved.data["messagingMode"], "manual")
        self.assertFalse(saved.data["connected"])

        prepared = self.client.post("/api/v1/whatsapp/manual-share", {
            "targetType": "customer", "targetId": str(self.customer.pk),
        }, format="json")
        self.assertEqual(prepared.status_code, 200, prepared.data)
        self.assertTrue(prepared.data["url"].startswith("https://wa.me/919888888888?text="))
        self.assertIn("%3A%2F%2F", prepared.data["url"])
        self.assertEqual(prepared.data["status"], "manual_prepared")
        self.assertIn("press Send", prepared.data["note"])
        self.assertEqual(
            WhatsAppNotificationLog.objects.get(company=self.company).status,
            "manual_prepared",
        )

    def test_switching_modes_controls_customer_event_queue(self):
        conn = WhatsAppBusinessConnection.objects.create(
            company=self.company, connected=True, messaging_mode="manual",
            notifications_enabled=True, event_settings={"job_created": True},
        )
        CustomerNotificationPreference.objects.create(
            company=self.company, customer=self.customer, whatsapp_opt_in=True,
            consent_source="Signed intake form",
        )
        WhatsAppMessageTemplate.objects.create(
            company=self.company, event="job_created",
            template_name="approved_job", approval_status="APPROVED",
        )
        self.assertIsNone(queue_customer_notification(self.customer, "job_created", "mode:manual"))
        reply = self.client.patch("/api/v1/whatsapp/settings", {
            "messagingMode": "automatic", "notificationsEnabled": True,
        }, format="json")
        self.assertEqual(reply.status_code, 200, reply.data)
        self.assertEqual(reply.data["messagingMode"], "automatic")
        queued = queue_customer_notification(self.customer, "job_created", "mode:automatic")
        self.assertIsNotNone(queued)
        self.assertEqual(queued.status, "failed")  # Sending disabled in these tests.
        conn.refresh_from_db()
        conn.connected = False
        conn.save(update_fields=["connected"])
        disconnected = queue_customer_notification(self.customer, "job_created", "mode:disconnected")
        self.assertEqual(disconnected.status, "failed")
        self.assertIn("disconnected", disconnected.error_detail)
        self.client.patch("/api/v1/whatsapp/settings", {
            "messagingMode": "manual",
        }, format="json")
        self.assertIsNone(queue_customer_notification(self.customer, "job_created", "mode:manual-again"))

    def test_manual_share_validates_international_number_and_tenant(self):
        self.customer.phone = "9888888888"
        self.customer.save(update_fields=["phone"])
        invalid = self.client.post("/api/v1/whatsapp/manual-share", {
            "targetType": "customer", "targetId": str(self.customer.pk),
        }, format="json")
        self.assertEqual(invalid.status_code, 400)
        other = Company.objects.create(name="Other Workshop", slug="wa-other-tenant")
        foreign_customer = Customer.objects.create(
            company=other, name="Other Customer", phone="+919777777777",
        )
        forbidden = self.client.post("/api/v1/whatsapp/manual-share", {
            "targetType": "customer", "targetId": str(foreign_customer.pk),
        }, format="json")
        self.assertEqual(forbidden.status_code, 404)

    def test_failed_automatic_message_exposes_manual_fallback_without_marking_sent(self):
        log = WhatsAppNotificationLog.objects.create(
            company=self.company, customer=self.customer,
            event="job_created", idempotency_key="failed:fallback",
            phone="919888888888", status="failed",
            error_detail="WhatsApp API disconnected.",
        )
        prepared = self.client.post("/api/v1/whatsapp/manual-share", {
            "targetType": "log", "targetId": str(log.pk),
        }, format="json")
        self.assertEqual(prepared.status_code, 200, prepared.data)
        log.refresh_from_db()
        self.assertEqual(log.status, "failed")
        self.assertEqual(
            WhatsAppNotificationLog.objects.filter(company=self.company, status="manual_prepared").count(), 1
        )

    @override_settings(WHATSAPP_SEND_ENABLED=True, CELERY_BROKER_URL="redis://localhost:6379/1")
    def test_automatic_worker_sends_template_once_and_does_not_retry(self):
        from apps.whatsapp.tasks import send_whatsapp_notification
        WhatsAppBusinessConnection.objects.create(
            company=self.company, connected=True, messaging_mode="automatic",
            notifications_enabled=True, event_settings={"job_created": True},
            phone_number_id="987654321",
            encrypted_access_token="encrypted-in-test",
        )
        WhatsAppMessageTemplate.objects.create(
            company=self.company, event="job_created", template_name="approved_job",
            approval_status="APPROVED",
        )
        CustomerNotificationPreference.objects.create(
            company=self.company, customer=self.customer, whatsapp_opt_in=True,
            consent_source="Customer signed service consent",
        )
        log = queue_customer_notification(self.customer, "job_created", "worker:once")
        self.assertEqual(log.status, "queued")
        with patch("apps.whatsapp.tasks.approved_templates", return_value=[
            {"name": "approved_job", "language": "en_US"}
        ]), patch("apps.whatsapp.tasks.decrypt_token", return_value="mock-access-token"), patch(
            "apps.whatsapp.tasks.send_template_message",
            return_value={"messages": [{"id": "wamid.mock-once"}]},
        ) as send:
            self.assertEqual(send_whatsapp_notification.run(str(log.pk)), "sent")
            self.assertEqual(send_whatsapp_notification.run(str(log.pk)), "sent")
            self.assertEqual(send.call_count, 1)
        log.refresh_from_db()
        self.assertEqual(log.status, "sent")
        self.assertEqual(log.meta_message_id, "wamid.mock-once")
        self.assertEqual(log.attempt_count, 1)
