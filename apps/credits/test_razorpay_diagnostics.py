"""Razorpay error diagnostics: do not leak merchant secrets or create orders in probes."""
from io import StringIO
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from apps.saas.razorpay_client import GatewayError, api_request


@override_settings(
    RAZORPAY_KEY_ID="rzp_test_example_only",
    RAZORPAY_KEY_SECRET="example-dummy-private-value",
    RAZORPAY_WEBHOOK_SECRET="dummy-webhook-private-value",
)
class RazorpayDiagnosticsTests(SimpleTestCase):
    def test_unauthorized_response_is_classified_without_leaking_api_secrets(self):
        err = HTTPError(
            "https://api.razorpay.com/v1/orders",
            401, "private upstream details", hdrs=None, fp=None,
        )
        with patch("apps.saas.razorpay_client.urlopen", side_effect=err):
            with self.assertRaises(GatewayError) as context:
                api_request("POST", "orders", {"amount": 100, "currency": "INR"})
        exception = context.exception
        self.assertEqual(exception.reason, "invalid_credentials")
        self.assertEqual(exception.status_code, 401)
        self.assertNotIn("example-dummy-private-value", str(exception))
        self.assertNotIn("private upstream details", str(exception))

    def test_gateway_rejection_has_safe_status_only(self):
        err = HTTPError("https://api.razorpay.com/v1/orders", 400,
                        "should never be logged verbatim", hdrs=None, fp=None)
        with patch("apps.saas.razorpay_client.urlopen", side_effect=err):
            with self.assertRaises(GatewayError) as context:
                api_request("POST", "orders", {"amount": 100, "currency": "INR"})
        self.assertEqual(context.exception.reason, "provider_rejected")
        self.assertEqual(context.exception.status_code, 400)
        self.assertNotIn("should never be logged verbatim", str(context.exception))

    def test_network_failure_is_distinct_from_bad_credentials(self):
        with patch("apps.saas.razorpay_client.urlopen",
                   side_effect=URLError("network unavailable")):
            with self.assertRaises(GatewayError) as context:
                api_request("GET", "orders?count=1")
        self.assertEqual(context.exception.reason, "network")
        self.assertIsNone(context.exception.status_code)

    def test_check_without_probe_does_not_call_provider_or_print_secrets(self):
        stdout = StringIO()
        with patch("apps.credits.management.commands.check_razorpay.api_request") as fn:
            call_command("check_razorpay", stdout=stdout)
        fn.assert_not_called()
        result = stdout.getvalue()
        self.assertIn("Razorpay key mode: test", result)
        self.assertNotIn("example-dummy-private-value", result)
        self.assertNotIn("dummy-webhook-private-value", result)

    def test_readonly_probe_uses_get_and_does_not_create_order(self):
        stdout = StringIO()
        with patch("apps.credits.management.commands.check_razorpay.api_request",
                   return_value={"items": []}) as fn:
            call_command("check_razorpay", probe=True, stdout=stdout)
        fn.assert_called_once_with("GET", "orders?count=1")
        self.assertIn("authentication OK", stdout.getvalue())

    def test_failed_probe_reports_only_safe_error_category(self):
        stdout = StringIO()
        with patch("apps.credits.management.commands.check_razorpay.api_request",
                   side_effect=GatewayError("Unauthorized", reason="invalid_credentials",
                                            status_code=401)):
            with self.assertRaises(CommandError) as context:
                call_command("check_razorpay", probe=True, stdout=stdout)
        self.assertIn("HTTP 401", str(context.exception))
        self.assertNotIn("example-dummy-private-value", str(context.exception))


class MissingRazorpayConfigTests(SimpleTestCase):
    @override_settings(RAZORPAY_KEY_ID="", RAZORPAY_KEY_SECRET="",
                       RAZORPAY_WEBHOOK_SECRET="")
    def test_missing_credentials_fail_safely(self):
        with self.assertRaises(CommandError):
            call_command("check_razorpay", stdout=StringIO())
