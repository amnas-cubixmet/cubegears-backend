"""Scheduler opt-in and broker-free command safety."""
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, override_settings


class AutoCheckoutSchedulingTests(SimpleTestCase):
    @override_settings(ATTENDANCE_AUTO_CHECKOUT_ENABLED=False)
    def test_management_command_disabled(self):
        with self.assertRaises(CommandError):
            call_command("process_auto_checkout", "--run")

    @override_settings(ATTENDANCE_AUTO_CHECKOUT_ENABLED=True)
    def test_management_command_no_run_is_read_only(self):
        with patch("apps.attendance.management.commands.process_auto_checkout.auto_close_attendance_sessions") as task:
            call_command("process_auto_checkout")
            task.run.assert_not_called()

    @override_settings(ATTENDANCE_AUTO_CHECKOUT_ENABLED=True)
    def test_scheduled_management_command_runs_locally(self):
        with patch("apps.attendance.management.commands.process_auto_checkout.auto_close_attendance_sessions") as task:
            task.run.return_value = {"closed": 0}
            call_command("process_auto_checkout", "--run")
            task.run.assert_called_once_with()

    @override_settings(ATTENDANCE_AUTO_CHECKOUT_ENABLED=False)
    def test_celery_task_fails_closed_without_flag(self):
        from apps.attendance.tasks import auto_close_attendance_sessions
        self.assertEqual(
            auto_close_attendance_sessions.run(), {"disabled": True, "closed": 0}
        )
