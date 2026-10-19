from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings


@override_settings(
    EMAIL_CELERY_ENABLED=True,
    CELERY_BROKER_URL='redis://127.0.0.1:6379/0',
    EMAIL_BACKEND='django.core.mail.backends.console.EmailBackend',
)
class EmailWorkerCommandTests(SimpleTestCase):
    def run_command(self):
        call_command('start_email_worker', stdout=StringIO(), stderr=StringIO())

    @override_settings(EMAIL_CELERY_ENABLED=False)
    @patch('config.celery.app.worker_main')
    def test_disabled_email_does_not_start_worker(self, worker):
        with self.assertRaisesMessage(CommandError, 'EMAIL_CELERY_ENABLED=True'):
            self.run_command()
        worker.assert_not_called()

    @override_settings(CELERY_BROKER_URL='')
    @patch('config.celery.app.worker_main')
    def test_missing_broker_does_not_fall_back_to_rabbitmq(self, worker):
        with self.assertRaisesMessage(CommandError, 'CELERY_BROKER_URL=redis://'):
            self.run_command()
        worker.assert_not_called()

    @patch('config.celery.app.worker_main')
    @patch('config.celery.app.connection_for_write', side_effect=OSError('private-secret'))
    def test_unreachable_redis_stops_without_leaking_credentials(self, connection, worker):
        with self.assertRaisesMessage(CommandError, 'Redis is unavailable (OSError)') as error:
            self.run_command()
        self.assertNotIn('private-secret', str(error.exception))
        worker.assert_not_called()

    @patch('apps.accounts.management.commands.start_email_worker.sys.platform', 'win32')
    @patch('config.celery.app.worker_main')
    @patch('config.celery.app.connection_for_write')
    def test_windows_starts_solo_worker_after_broker_check(self, connection, worker):
        self.run_command()
        connection.return_value.__enter__.return_value.ensure_connection.assert_called_once_with(
            max_retries=0, timeout=3)
        worker.assert_called_once_with(['worker', '--loglevel=info', '--pool=solo'])

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend', EMAIL_HOST='')
    @patch('config.celery.app.worker_main')
    def test_incomplete_smtp_stops_before_worker_start(self, worker):
        with self.assertRaisesMessage(CommandError, 'SMTP settings are incomplete'):
            self.run_command()
        worker.assert_not_called()
