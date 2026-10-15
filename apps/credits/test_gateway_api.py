"""Credit gateway diagnostics must be admin-only, read-only and secret-free."""
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.companies.models import Company
from apps.roles.models import Role
from apps.saas.razorpay_client import GatewayError


@override_settings(
    RAZORPAY_KEY_ID="rzp_test_dummy_for_tests",
    RAZORPAY_KEY_SECRET="dummy-private-test-key",
    RAZORPAY_WEBHOOK_SECRET="dummy-private-webhook-key",
)
class GatewayDiagnosticsApiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.company = Company.objects.create(name="Demo Workshop", slug="gateway-demo")
        role = Role.objects.create(
            company=self.company, name="Billing Admin", code="ADMIN",
            permissions=["billing.view", "billing.edit"],
        )
        self.admin = User.objects.create_user(
            email="billing-gateway@example.test", name="Billing Admin",
            password="SafeTestPassword123!", company=self.company, role=role,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def test_status_returns_only_safe_configuration_flags(self):
        response = self.client.get("/api/v1/saas/credits/gateway/status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["mode"], "test")
        self.assertTrue(response.data["configured"])
        self.assertTrue(response.data["webhookSecretPresent"])
        self.assertTrue(response.data["probeAvailable"])
        self.assertNotIn("dummy-private-test-key", response.content.decode())
        self.assertNotIn("dummy-private-webhook-key", response.content.decode())

    def test_staff_without_billing_permission_cannot_probe(self):
        role = Role.objects.create(
            company=self.company, name="Mechanic", code="MECHANIC",
            permissions=["jobs.view"],
        )
        staff = User.objects.create_user(
            email="mechanic-gateway@example.test", name="Mechanic",
            password="SafeTestPassword123!", company=self.company, role=role,
        )
        self.client.force_authenticate(staff)
        self.assertEqual(self.client.get("/api/v1/saas/credits/gateway/status").status_code, 403)
        with patch("apps.credits.gateway_diagnostics.api_request") as provider:
            result = self.client.post("/api/v1/saas/credits/gateway/test", {}, format="json")
        self.assertEqual(result.status_code, 403)
        provider.assert_not_called()

    def test_probe_is_readonly_and_does_not_create_orders(self):
        with patch("apps.credits.gateway_diagnostics.api_request", return_value={"items": []}) as provider:
            result = self.client.post("/api/v1/saas/credits/gateway/test", {}, format="json")
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.data["ok"])
        provider.assert_called_once_with("GET", "orders?count=1")
        self.assertNotIn("dummy-private-test-key", result.content.decode())

    def test_probe_rejects_live_mode_without_provider_call(self):
        with override_settings(RAZORPAY_KEY_ID="rzp_live_dummy_for_tests"):
            with patch("apps.credits.gateway_diagnostics.api_request") as provider:
                result = self.client.post("/api/v1/saas/credits/gateway/test", {}, format="json")
        self.assertEqual(result.status_code, 409)
        self.assertEqual(result.data["code"], "TEST_MODE_REQUIRED")
        provider.assert_not_called()

    def test_probe_classifies_bad_credentials_without_leaking_secret(self):
        with patch("apps.credits.gateway_diagnostics.api_request", side_effect=GatewayError(
            reason="invalid_credentials", status_code=401
        )) as provider:
            result = self.client.post("/api/v1/saas/credits/gateway/test", {}, format="json")
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.data["code"], "RAZORPAY_AUTH_FAILED")
        self.assertNotIn("dummy-private-test-key", result.content.decode())
        provider.assert_called_once_with("GET", "orders?count=1")

    @override_settings(RAZORPAY_KEY_ID="", RAZORPAY_KEY_SECRET="",
                       RAZORPAY_WEBHOOK_SECRET="your_webhook_secret")
    def test_unconfigured_status_and_placeholder_webhook_are_not_ready(self):
        response = self.client.get("/api/v1/saas/credits/gateway/status")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["configured"])
        self.assertFalse(response.data["webhookSecretPresent"])
        self.assertFalse(response.data["probeAvailable"])
