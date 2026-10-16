"""Celery-only account mail: web process cannot deliver on its own."""
from unittest.mock import patch

from django.core import mail
from django.db import transaction
from django.test import TestCase, override_settings

from apps.accounts.email import email_delivery_mode, queue_email
from apps.accounts.tasks import send_email_task


@override_settings(
    EMAIL_CELERY_ENABLED=True,
    CELERY_BROKER_URL="redis://127.0.0.1:6379/0",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class CeleryOnlyMailTests(TestCase):
    @patch("config.celery.app.send_task")
    def test_plain_notification_queues_after_commit_without_sending(self, enqueue):
        with self.captureOnCommitCallbacks(execute=True):
            state = queue_email("Statement", "Available", ["x@example.test"])
            enqueue.assert_not_called()
            self.assertEqual(state["status"], "pending")
        self.assertEqual(state["status"], "queued")
        enqueue.assert_called_once()
        self.assertEqual(enqueue.call_args.args[0], send_email_task.name)
        self.assertEqual(enqueue.call_args.kwargs["args"][2], ["x@example.test"])
        self.assertEqual(len(mail.outbox), 0)

    @patch("config.celery.app.send_task")
    def test_action_url_and_callback_go_to_worker_not_request_smtp(self, enqueue):
        callbacks = []
        with self.captureOnCommitCallbacks(execute=True):
            state = queue_email(
                "Secure setup", "Set your password", ["x@example.test"],
                action_url="https://example.test/setup?token=secret",
                action_label="Set password",
                on_delivery=callbacks.append,
            )
        self.assertEqual(state["status"], "queued")
        self.assertEqual(callbacks, ["queued"])
        self.assertEqual(enqueue.call_args.kwargs["args"][4], "https://example.test/setup?token=secret")
        self.assertEqual(len(mail.outbox), 0)

    @patch("config.celery.app.send_task", side_effect=OSError("redis offline"))
    def test_redis_failure_never_falls_back_to_smtp(self, enqueue):
        callbacks = []
        with self.assertLogs("apps.accounts.email", level="ERROR") as logs:
            with self.captureOnCommitCallbacks(execute=True):
                state = queue_email("Notification", "Hello", ["x@example.test"],
                                    on_delivery=callbacks.append)
        enqueue.assert_called_once()
        self.assertEqual(state["status"], "failed")
        self.assertEqual(callbacks, ["failed"])
        self.assertEqual(len(mail.outbox), 0)
        self.assertNotIn("Hello", " ".join(logs.output))

    @override_settings(EMAIL_CELERY_ENABLED=False)
    @patch("config.celery.app.send_task")
    def test_celery_disabled_prevents_all_email(self, enqueue):
        self.assertEqual(email_delivery_mode(), "disabled")
        with self.captureOnCommitCallbacks(execute=True):
            state = queue_email("Hello", "Body", ["x@example.test"])
        self.assertEqual(state["status"], "disabled")
        enqueue.assert_not_called()
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(CELERY_BROKER_URL="")
    @patch("config.celery.app.send_task")
    def test_missing_broker_prevents_all_email(self, enqueue):
        with self.captureOnCommitCallbacks(execute=True):
            state = queue_email("Hello", "Body", ["x@example.test"])
        self.assertEqual(state["status"], "disabled")
        enqueue.assert_not_called()
        self.assertEqual(len(mail.outbox), 0)

    @patch("config.celery.app.send_task")
    def test_rollback_never_queues(self, enqueue):
        with self.captureOnCommitCallbacks(execute=True):
            try:
                with transaction.atomic():
                    queue_email("Hello", "Body", ["x@example.test"])
                    raise ValueError("rolled back")
            except ValueError:
                pass
        enqueue.assert_not_called()
        self.assertEqual(len(mail.outbox), 0)

    def test_worker_renders_branded_action_email(self):
        # Simulate execution IN the task, not inside queue_email or a view.
        result = send_email_task.run(
            "Secure setup", "Set password", ["x@example.test"], None,
            "https://example.test/setup?token=abc", "Set password"
        )
        self.assertTrue(result["acceptedByProvider"])
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("https://example.test/setup?token=abc", mail.outbox[0].body)
        self.assertEqual(len(mail.outbox[0].alternatives), 1)

    def test_worker_delivers_plain_email(self):
        result = send_email_task.run("Notice", "Body", ["x@example.test"])
        self.assertTrue(result["acceptedByProvider"])
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(EMAIL_CELERY_ENABLED=False)
    def test_worker_respects_disabled_mail_flag(self):
        self.assertEqual(send_email_task.run("Hello", "Body", ["x@example.test"]), {"disabled": True})
        self.assertEqual(len(mail.outbox), 0)
