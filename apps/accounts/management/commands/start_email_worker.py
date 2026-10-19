"""Start a mail worker with the same .env/settings as manage.py."""
import sys

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from config import celery_app


class Command(BaseCommand):
    help = 'Validate Redis/SMTP configuration and start the Celery worker (Windows: solo pool).'

    def handle(self, *args, **options):
        if not settings.EMAIL_CELERY_ENABLED:
            raise CommandError('Set EMAIL_CELERY_ENABLED=True in backend .env, then restart Django.')
        if not settings.CELERY_BROKER_URL.startswith(('redis://', 'rediss://')):
            raise CommandError('Set CELERY_BROKER_URL=redis://127.0.0.1:6379/0 in backend .env.')
        # Reuse the existing diagnostics without sending a test message.
        call_command('check_email_delivery', stdout=self.stdout, stderr=self.stderr)
        try:
            with celery_app.connection_for_write(connect_timeout=3) as connection:
                connection.ensure_connection(max_retries=0, timeout=3)
        except Exception as exc:
            raise CommandError(
                f'Redis is unavailable ({type(exc).__name__}). Start Redis before the worker.'
            ) from None
        worker_args = ['worker', '--loglevel=info']
        if sys.platform == 'win32':
            worker_args.append('--pool=solo')
        self.stdout.write('Starting worker. Keep this terminal open; Beat is not required for staff invitations.')
        celery_app.worker_main(worker_args)
