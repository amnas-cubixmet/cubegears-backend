"""Optional Celery mail only queues safe notifications; auth callback remains direct."""
from unittest.mock import patch
from django.test import TestCase, override_settings
from apps.accounts.email import queue_email


@override_settings(EMAIL_CELERY_ENABLED=True,
                   EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class CeleryMailCompatibilityTests(TestCase):
    @patch("apps.accounts.tasks.send_email_task.delay")
    def test_plain_notification_queues_only_after_commit(self, deferred):
        with self.captureOnCommitCallbacks(execute=True):
            state = queue_email("Monthly statement", "Statement available", ["x@example.test"])
            deferred.assert_not_called()
        deferred.assert_called_once()
        self.assertEqual(state["status"], "queued")

    @patch("apps.accounts.email.send_mail", return_value=1)
    @patch("apps.accounts.tasks.send_email_task.delay", side_effect=OSError("redis offline"))
    def test_broker_failure_falls_back_to_existing_direct_delivery(self, deferred, send):
        with self.captureOnCommitCallbacks(execute=True):
            state = queue_email("Notification", "Safe message", ["x@example.test"])
        deferred.assert_called_once()
        send.assert_called_once()
        self.assertEqual(state["status"], "local_only")

    @patch("apps.accounts.email.send_mail", return_value=1)
    @patch("apps.accounts.tasks.send_email_task.delay")
    def test_delivery_callbacks_never_depend_on_broker(self, deferred, send):
        notified = []
        with self.captureOnCommitCallbacks(execute=True):
            queue_email("Security notice", "Important", ["x@example.test"],
                        on_delivery=notified.append)
        deferred.assert_not_called()
        send.assert_called_once()
        self.assertEqual(notified, ["local_only"])
