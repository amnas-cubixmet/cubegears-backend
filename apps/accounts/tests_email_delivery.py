"""Email dispatch regressions; no live SMTP or Redis needed."""
from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.accounts.email import email_delivery_mode, queue_email


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
