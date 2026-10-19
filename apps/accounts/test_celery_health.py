from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings


@override_settings(EMAIL_CELERY_ENABLED=True, CREDIT_BILLING_CELERY_ENABLED=False,
                   CELERY_BROKER_URL='redis://127.0.0.1:6379/0', CELERY_BEAT_SCHEDULE={})
class CeleryHealthTests(SimpleTestCase):
    def test_shared_email_task_is_bound_to_project_app(self):
        from config import celery_app
        from apps.accounts.tasks import send_email_task
        self.assertIs(send_email_task.app, celery_app)

    @override_settings(CELERY_BROKER_URL='')
    @patch('config.celery.app.connection_for_write')
    def test_missing_broker_fails_without_connecting(self, connection):
        with self.assertRaises(CommandError):
            call_command('check_celery', stdout=StringIO(), stderr=StringIO())
        connection.assert_not_called()

    @patch('config.celery.app.control.inspect')
    @patch('config.celery.app.connection_for_write')
    def test_broker_without_worker_is_not_reported_ready(self, connection, inspect):
        inspect.return_value.ping.return_value = {}
        output = StringIO()
        with self.assertRaises(CommandError):
            call_command('check_celery', '--json', stdout=output)
        self.assertIn('"brokerReachable": true', output.getvalue())
        self.assertIn('"workersReady": false', output.getvalue())

    @patch('config.celery.app.control.inspect')
    @patch('config.celery.app.connection_for_write')
    def test_live_worker_must_register_email_task(self, connection, inspect):
        worker = inspect.return_value
        worker.ping.return_value = {'worker': {'ok': 'pong'}}
        worker.registered.return_value = {'worker': []}
        with self.assertRaises(CommandError):
            call_command('check_celery', stdout=StringIO(), stderr=StringIO())
        worker.registered.return_value = {'worker': ['apps.accounts.tasks.send_email_task']}
        output = StringIO()
        call_command('check_celery', '--json', stdout=output)
        self.assertIn('"workersReady": true', output.getvalue())

    @override_settings(CELERY_BEAT_SCHEDULE={'billing': {'task': 'credits.collect_due_monthly_bills'}})
    @patch('config.celery.app.control.inspect')
    @patch('config.celery.app.connection_for_write')
    def test_enabled_schedule_tasks_are_also_required(self, connection, inspect):
        worker = inspect.return_value
        worker.ping.return_value = {'worker': {'ok': 'pong'}}
        worker.registered.return_value = {'worker': ['apps.accounts.tasks.send_email_task']}
        output = StringIO()
        with self.assertRaises(CommandError):
            call_command('check_celery', '--json', stdout=output)
        self.assertIn('credits.collect_due_monthly_bills', output.getvalue())

    @patch('config.celery.app.control.inspect')
    @patch('config.celery.app.connection_for_write', side_effect=OSError('redis://user:password@private-host'))
    def test_broker_failure_does_not_leak_credentials_or_inspect_workers(self, connection, inspect):
        output = StringIO()
        with self.assertRaises(CommandError):
            call_command('check_celery', '--json', stdout=output)
        self.assertNotIn('password', output.getvalue())
        self.assertNotIn('private-host', output.getvalue())
        inspect.assert_not_called()
