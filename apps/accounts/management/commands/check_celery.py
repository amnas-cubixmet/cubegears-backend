"""Read-only broker/worker health check; never enqueue email or billing jobs."""
import json
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from config import celery_app


class Command(BaseCommand):
    help = 'Check Redis connectivity, live Celery workers and registered task names.'

    def add_arguments(self, parser):
        parser.add_argument('--timeout', type=float, default=3)
        parser.add_argument('--json', action='store_true', dest='as_json')

    def handle(self, *args, **options):
        timeout = options['timeout']
        if not 0 < timeout <= 10:
            raise CommandError('Timeout must be greater than 0 and at most 10 seconds.')
        report = {'brokerReachable': False, 'workersReady': False,
                  'emailEnabled': settings.EMAIL_CELERY_ENABLED,
                  'billingEnabled': settings.CREDIT_BILLING_CELERY_ENABLED,
                  'workers': [], 'missingTasks': [], 'errors': []}
        broker = settings.CELERY_BROKER_URL
        if not broker or not broker.startswith(('redis://', 'rediss://')):
            report['errors'].append('Configure an explicit Redis CELERY_BROKER_URL in the backend environment.')
        else:
            try:
                with celery_app.connection_for_write(connect_timeout=timeout) as connection:
                    connection.ensure_connection(max_retries=0, timeout=timeout)
                report['brokerReachable'] = True
            except Exception as exc:
                # Do not include URLs, credentials or provider exception messages.
                report['errors'].append(f'Redis connection failed ({type(exc).__name__}).')
        if report['brokerReachable']:
            try:
                inspector = celery_app.control.inspect(timeout=timeout)
                pings = inspector.ping() or {}
                live = sorted(name for name, value in pings.items() if value.get('ok') == 'pong')
                report['workers'] = live
                if not live:
                    report['errors'].append('No worker replied. Start celery -A config worker --loglevel=info.')
                else:
                    required = {entry['task'] for entry in settings.CELERY_BEAT_SCHEDULE.values()}
                    if settings.EMAIL_CELERY_ENABLED:
                        required.add('apps.accounts.tasks.send_email_task')
                    registered = (inspector.registered() or {}) if required else {}
                    missing = {name: sorted(required - set(registered.get(name, []))) for name in live}
                    report['missingTasks'] = [{'worker': name, 'tasks': tasks} for name, tasks in missing.items() if tasks]
                    if report['missingTasks']:
                        report['errors'].append('A worker is missing required tasks. Restart workers with this application version.')
                    else:
                        report['workersReady'] = True
            except Exception as exc:
                report['errors'].append(f'Worker inspection failed ({type(exc).__name__}).')
        if options['as_json']:
            self.stdout.write(json.dumps(report, sort_keys=True))
        else:
            self.stdout.write(f"Redis reachable: {report['brokerReachable']}; workers ready: {report['workersReady']}")
            self.stdout.write(f"Email enabled: {report['emailEnabled']}; billing enabled: {report['billingEnabled']}")
            for error in report['errors']:
                self.stderr.write(error)
        if report['errors']:
            raise CommandError('Celery health check failed. No emails or billing tasks were executed.')
