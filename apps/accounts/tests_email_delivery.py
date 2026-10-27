"""Email dispatch regressions; no live SMTP or Redis needed."""
from unittest.mock import patch

from django.core import mail
from django.test import TestCase, override_settings

from apps.accounts.email import email_delivery_mode, queue_email
from apps.accounts.email_transport import send_account_email


class EmailDeliveryModeTests(TestCase):
    @override_settings(ACCOUNT_EMAIL_DELIVERY_MODE="disabled")
    def test_disabled_mode_does_not_dispatch(self):
        with patch("apps.accounts.email.send_account_email") as sender:
            outcome = queue_email("subject", "body", ["a@example.test"])
            with self.captureOnCommitCallbacks(execute=True):
                pass
            self.assertEqual(outcome["status"], "disabled")
            sender.assert_not_called()

    @override_settings(
        ACCOUNT_EMAIL_DELIVERY_MODE="smtp",
        EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
        EMAIL_HOST="smtp.example.test", EMAIL_HOST_USER="service@example.test",
        EMAIL_HOST_PASSWORD="test-secret", DEFAULT_FROM_EMAIL="service@example.test",
        EMAIL_USE_TLS=True, EMAIL_USE_SSL=False,
    )
    def test_smtp_mode_dispatches_after_commit(self):
        with patch("apps.accounts.email.send_account_email", return_value={"acceptedByProvider": True}) as sender:
            with self.captureOnCommitCallbacks(execute=True):
                result = queue_email("subject", "body", ["a@example.test"], action_url="https://example.test/reset")
                sender.assert_not_called()
            self.assertEqual(result["status"], "sent")
            sender.assert_called_once()

    @override_settings(
        ACCOUNT_EMAIL_DELIVERY_MODE="smtp",
        EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
        EMAIL_HOST="smtp.example.test", EMAIL_HOST_USER="service@example.test",
        EMAIL_HOST_PASSWORD="test-secret", DEFAULT_FROM_EMAIL="service@example.test",
    )
    def test_smtp_failure_is_logged_as_failure_not_delivered(self):
        with patch("apps.accounts.email.send_account_email", side_effect=OSError("secret")):
            with self.captureOnCommitCallbacks(execute=True):
                result = queue_email("subject", "body", ["a@example.test"])
            self.assertEqual(result["status"], "failed")

    @override_settings(
        ACCOUNT_EMAIL_DELIVERY_MODE="celery",
        EMAIL_CELERY_ENABLED=True,
        CELERY_BROKER_URL="redis://example.invalid:6379/0",
    )
    def test_celery_mode_queues_without_sync_smtp(self):
        with patch("config.celery.app.send_task") as queued:
            with self.captureOnCommitCallbacks(execute=True):
                result = queue_email("subject", "body", ["a@example.test"])
            queued.assert_called_once()
            self.assertEqual(result["status"], "queued")

    @override_settings(
        ACCOUNT_EMAIL_DELIVERY_MODE="smtp",
        EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend",
    )
    def test_smtp_mode_fails_closed_with_console_backend(self):
        self.assertEqual(email_delivery_mode(), "disabled")


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class BrandedEmailTemplateTests(TestCase):
    """HTML + plaintext messages render correctly without network access."""

    def test_responsive_action_email_has_branded_button_and_text_fallback(self):
        url = "https://example.test/setup?token=abc&next=1"
        result = send_account_email(
            subject="Set up your CubeGears password",
            message="Welcome to your new account.\nChoose a secure password.",
            recipient_list=["member@example.test"],
            from_email="notifications@example.test",
            action_url=url,
            action_label="Set up password",
        )

        self.assertEqual(result, {"acceptedByProvider": True})
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.content_subtype, "plain")
        self.assertEqual(len(message.alternatives), 1)
        html = message.alternatives[0][0]
        self.assertIn("CubeGears", html)
        self.assertIn("Set up password", html)
        self.assertIn("href=\"https://example.test/setup?token=abc&amp;next=1\"", html)
        self.assertIn("@media screen and (max-width:620px)", html)
        self.assertIn("prefers-reduced-motion", html)
        self.assertIn("Never share login or password-reset links", html)
        self.assertIn(url, message.body)
        self.assertIn("CubeGears · Secure account notifications", message.body)

    def test_html_escapes_dynamic_content(self):
        send_account_email(
            subject="Welcome <script>alert(1)</script>",
            message="Hi <img src=x onerror=alert(1)>",
            recipient_list=["member@example.test"],
            action_url="https://example.test/setup?token=123",
            action_label="Open link",
        )
        html = mail.outbox[0].alternatives[0][0]
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertNotIn("<img src=x onerror=alert(1)>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("&lt;img", html)

    def test_non_action_email_remains_plain_text(self):
        send_account_email(
            subject="Account update",
            message="Your profile was changed.",
            recipient_list=["member@example.test"],
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].alternatives, [])
