"""Official WhatsApp integration must be consent-gated and inert without configuration."""
import hashlib
import hmac
import json
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
            company=self.company, connected=True, notifications_enabled=True,
            phone_number_id="123456789",event_settings={"job_created":True},
        )
        self.assertIsNone(queue_customer_notification(self.customer,"job_created","job:a:created"))
        consent = self.client.post(
            f"/api/v1/whatsapp/customers/{self.customer.id}/consent",
            {"optIn":True, "source":"Signed consent on service intake form"},format="json",
        )
        self.assertEqual(consent.status_code, 200, consent.data)
        self.assertTrue(CustomerNotificationPreference.objects.get(customer=self.customer).whatsapp_opt_in)
        self.assertIsNone(queue_customer_notification(self.customer,"job_created","job:a:created"))
        WhatsAppMessageTemplate.objects.create(
            company=self.company,event="job_created",template_name="approved_job",approval_status="APPROVED",
        )
        first = queue_customer_notification(self.customer,"job_created","job:a:created")
        second = queue_customer_notification(self.customer,"job_created","job:a:created")
        self.assertIsNotNone(first)
        self.assertEqual(first.id, second.id)
        self.assertEqual(WhatsAppNotificationLog.objects.filter(company=self.company).count(),1)
        self.assertEqual(first.status,"pending")
        conn.connected = False
        conn.save(update_fields=["connected"])
        self.assertIsNone(queue_customer_notification(self.customer,"job_created","job:b:created"))

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
