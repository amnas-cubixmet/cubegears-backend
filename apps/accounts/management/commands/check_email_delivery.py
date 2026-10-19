"""Check worker email configuration; optional diagnostics follow the Celery path."""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from apps.accounts.email import email_delivery_mode, queue_email


class Command(BaseCommand):
    help = 'Check account email configuration. Optional --to queues a test through Celery.'

    def add_arguments(self, parser):
        parser.add_argument('--to', dest='recipient', default=None)

    def handle(self, *args, **options):
        mode = email_delivery_mode()
        self.stdout.write(f'Email dispatch readiness: {mode}')
        self.stdout.write(f'Worker email backend: {settings.EMAIL_BACKEND}')
        self.stdout.write(f'Frontend link base: {settings.FRONTEND_URL}')
        if mode == 'disabled':
            message = 'Enable EMAIL_CELERY_ENABLED and configure a Redis CELERY_BROKER_URL; run the Celery worker.'
            if options['recipient']:
                raise CommandError(message)
            self.stdout.write(self.style.WARNING(message))
            return
        if not settings.EMAIL_BACKEND.endswith('.smtp.EmailBackend'):
            message = 'The worker is using a non-SMTP backend; real inbox delivery is not configured.'
            if options['recipient']:
                raise CommandError(message)
            self.stdout.write(self.style.WARNING(message))
            return
        if not all((settings.EMAIL_HOST, settings.EMAIL_HOST_USER, settings.EMAIL_HOST_PASSWORD, settings.DEFAULT_FROM_EMAIL)):
            raise CommandError('SMTP settings are incomplete. Configure host, sender and credentials privately in the worker environment.')
        if settings.EMAIL_USE_SSL and settings.EMAIL_USE_TLS:
            raise CommandError('Enable only one of EMAIL_USE_SSL or EMAIL_USE_TLS.')
        self.stdout.write('Celery and SMTP configuration is present; worker/provider delivery has not been verified.')
        if options['recipient']:
            try:
                validate_email(options['recipient'])
            except ValidationError:
                raise CommandError('Enter a valid recipient email.') from None
            result = queue_email('CubixGear email delivery test',
                'Worker email configuration test. No login links or passwords are included.', [options['recipient']])
            if result['status'] == 'pending':
                self.stdout.write('Test is pending transaction commit; dispatch has not happened yet.')
                return
            if result['status'] != 'queued':
                raise CommandError('Celery did not accept the test message. Check Redis and worker configuration.')
            self.stdout.write(self.style.SUCCESS('Test queued through Celery. Check worker logs, inbox and spam; queued does not mean delivered.'))
