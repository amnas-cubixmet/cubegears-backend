"""Manual-only WhatsApp sharing and historical-data regression coverage."""
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.roles.models import Role
from apps.saas.models import CompanySetting
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

    def test_manual_sharing_is_permanently_enabled_and_has_no_disable_option(self):
        settings_url = "/api/v1/whatsapp/settings"
        share_url = "/api/v1/whatsapp/manual-share"

        initial = self.client.get(settings_url)
        self.assertEqual(initial.status_code, 200, initial.data)
        self.assertTrue(initial.data["manualEnabled"])
        self.assertEqual(initial.data["manualStatus"], "enabled")
        self.assertFalse(initial.data["sendEnabled"])
        self.assertFalse(initial.data["notificationsEnabled"])
        self.assertEqual(initial.data["messagingMode"], "manual")

        for values in (
            {"manualEnabled": False},
            {"messagingMode": "automatic"},
            {"notificationsEnabled": True},
        ):
            result = self.client.patch(settings_url, values, format="json")
            self.assertEqual(result.status_code, 400, result.data)

        same = self.client.patch(
            settings_url, {"manualEnabled": True}, format="json"
        )
        self.assertEqual(same.status_code, 200, same.data)
        self.assertTrue(same.data["manualEnabled"])

        # Even old disabled settings cannot disable manual Click-to-Chat.
        CompanySetting.objects.create(
            company=self.company, category="whatsapp_manual",
            data={"enabled": False},
        )
        latest = self.client.get(settings_url)
        self.assertTrue(latest.data["manualEnabled"])
        draft = self.client.post(share_url, {
            "targetType": "customer",
            "targetId": str(self.customer.pk),
        }, format="json")
        self.assertEqual(draft.status_code, 200, draft.data)
        self.assertTrue(draft.data["url"].startswith("https://wa.me/"))
        self.assertEqual(draft.data["status"], "manual_prepared")

    def test_legacy_automatic_whatsapp_flags_cannot_be_enabled(self):
        # Historical settings may contain true. The API must not report an
        # automatic WhatsApp capability that no longer exists.
        CompanySetting.objects.create(
            company=self.company, category="notifications",
            data={"whatsapp": True},
        )
        CompanySetting.objects.create(
            company=self.company, category="integrations",
            data={"whatsappEnabled": True},
        )
        root = self.client.get("/api/v1/settings")
        self.assertEqual(root.status_code, 200, root.data)
        self.assertFalse(root.data["notifications"]["whatsapp"])
        self.assertFalse(root.data["integrations"]["whatsappEnabled"])
        for category, payload in (
            ("notifications", {"whatsapp": True}),
            ("integrations", {"whatsappEnabled": True}),
        ):
            url = "/api/v1/settings/" + category
            self.assertFalse(self.client.get(url).data[
                "whatsapp" if category == "notifications" else "whatsappEnabled"
            ])
            result = self.client.put(url, payload, format="json")
            self.assertEqual(result.status_code, 400, result.data)

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

    def test_invalid_record_id_is_a_safe_400_not_internal_server_error(self):
        for value in ("invalid-id", None, "<script>alert(1)</script>"):
            result = self.client.post("/api/v1/whatsapp/manual-share", {
                "targetType": "customer", "targetId": value,
            }, format="json")
            self.assertEqual(result.status_code, 400)
            self.assertIn("valid record", result.data["message"])

    def test_manual_share_does_not_require_meta_and_recovers_customer_contact(self):
        self.customer.whatsapp = "invalid"
        self.customer.save(update_fields=["whatsapp"])
        result = self.client.post("/api/v1/whatsapp/manual-share", {
            "targetType": "customer", "targetId": str(self.customer.pk),
        }, format="json")
        self.assertEqual(result.status_code, 200, result.data)
        self.assertIn("wa.me/919888888888", result.data["url"])

    def test_legacy_indian_mobile_works_for_an_indian_workshop(self):
        self.customer.phone = "9888888888"
        self.customer.save(update_fields=["phone"])
        result = self.client.post("/api/v1/whatsapp/manual-share", {
            "targetType": "customer", "targetId": str(self.customer.pk),
        }, format="json")
        self.assertEqual(result.status_code, 200, result.data)
        self.assertIn("wa.me/919888888888", result.data["url"])

    def test_invalid_number_and_tenant_isolation(self):
        # A non-Indian workshop must not silently send to an Indian number.
        self.company.country = "United Arab Emirates"
        self.company.save(update_fields=["country"])
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

    def test_payment_reminder_only_for_outstanding_finalized_invoice(self):
        from datetime import date
        from decimal import Decimal
        from django.utils import timezone
        from apps.invoices.models import Invoice

        invoice = Invoice.objects.create(
            company=self.company, customer=self.customer,
            date=date.today(), number="INV-MANUAL-001",
            status="Finalized", finalized_at=timezone.now(),
            total=Decimal("1500"), paid=Decimal("500"), balance=Decimal("1000"),
        )
        result = self.client.post("/api/v1/whatsapp/manual-share", {
            "targetType": "payment_reminder", "targetId": str(invoice.pk)
        }, format="json")
        self.assertEqual(result.status_code, 200, result.data)
        self.assertIn("INV-MANUAL-001", result.data["preview"])
        self.assertIn("1000", result.data["preview"])
        self.assertEqual(result.data["status"], "manual_prepared")
        invoice.balance = Decimal("0")
        invoice.save(update_fields=["balance"])
        declined = self.client.post("/api/v1/whatsapp/manual-share", {
            "targetType": "payment_reminder", "targetId": str(invoice.pk)
        }, format="json")
        self.assertEqual(declined.status_code, 409)

    def test_phone_normalization_covers_supported_countries(self):
        self.assertEqual(normalize_recipient("+91 (98765) 43210"), "919876543210")
        self.assertEqual(normalize_recipient("+971 501234567"), "971501234567")
        self.assertEqual(normalize_recipient("+974 12345678"), "97412345678")
        self.assertEqual(normalize_recipient("9876543210"), "")
        self.assertEqual(normalize_recipient("9876543210", country="India"), "919876543210")
        self.assertEqual(normalize_recipient("9876543210", country="United Arab Emirates"), "")
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
