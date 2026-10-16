"""Exercise a real private Redis + Celery worker without SMTP or tenant billing.

Requires redis-server on PATH (or --redis-server /path). All files, the broker
and worker belong to this test and are shut down afterwards. No existing Redis
instance is contacted, flushed or reconfigured.
"""
import argparse
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def stop(process):
    if process and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--redis-server', default=shutil.which('redis-server'))
    args = parser.parse_args()
    if not args.redis_server:
        parser.error('Install redis-server or pass its binary path with --redis-server.')
    with tempfile.TemporaryDirectory(prefix='cubegears-celery-') as folder:
        directory = Path(folder)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        broker = f'redis://127.0.0.1:{port}/0'
        mail_dir = directory / 'mail'
        mail_dir.mkdir()
        settings_source = '\n'.join([
            'from config.settings import *',
            f'CELERY_BROKER_URL = {broker!r}',
            f'CELERY_RESULT_BACKEND = {broker[:-1] + "1"!r}',
            'CELERY_TASK_IGNORE_RESULT = False',
            'CELERY_TASK_ALWAYS_EAGER = False',
            'EMAIL_CELERY_ENABLED = True',
            'CREDIT_BILLING_CELERY_ENABLED = False',
            'BILLING_EMAIL_ALERTS_ENABLED = False',
            'CELERY_BEAT_SCHEDULE = {}',
            "EMAIL_BACKEND = 'django.core.mail.backends.filebased.EmailBackend'",
            f'EMAIL_FILE_PATH = {str(mail_dir)!r}',
            "DEFAULT_FROM_EMAIL = 'celery-test@example.test'",
            f'DATABASES = {{"default": {{"ENGINE": "django.db.backends.sqlite3", "NAME": {str(directory / "unused.sqlite3")!r}}}}}',
        ])
        (directory / 'celery_smoke_settings.py').write_text(settings_source, encoding='utf-8')
        env = {**os.environ, 'DJANGO_SETTINGS_MODULE': 'celery_smoke_settings',
               'CELERY_BROKER_URL': broker, 'CREDIT_BILLING_CELERY_ENABLED': 'False',
               'PYTHONPATH': str(directory) + os.pathsep + str(ROOT)}
        redis_process = worker = None
        with (directory / 'redis.log').open('w+') as redis_log, (directory / 'worker.log').open('w+') as worker_log:
            try:
                redis_process = subprocess.Popen([
                    args.redis_server, '--bind', '127.0.0.1', '--port', str(port),
                    '--save', '', '--appendonly', 'no', '--dir', folder,
                ], stdout=redis_log, stderr=subprocess.STDOUT, env=env)
                import redis
                client = redis.Redis(host='127.0.0.1', port=port, socket_connect_timeout=1)
                for _ in range(50):
                    try:
                        if client.ping():
                            break
                    except redis.ConnectionError:
                        time.sleep(0.1)
                else:
                    raise RuntimeError('Private Redis did not start.')
                worker = subprocess.Popen([
                    sys.executable, '-m', 'celery', '-A', 'config', 'worker',
                    '--pool=solo', '--concurrency=1', '--loglevel=info',
                    '--without-gossip', '--without-mingle', '--without-heartbeat',
                    '--hostname=cubegears-smoke@localhost',
                ], cwd=ROOT, stdout=worker_log, stderr=subprocess.STDOUT, env=env)
                producer = r'''
import time
from pathlib import Path
import django
django.setup()
from django.conf import settings
from django.core.management import call_command
from config import celery_app
from apps.accounts.email import queue_email
from apps.accounts.tasks import send_email_task
assert send_email_task.app is celery_app
for attempt in range(20):
    if celery_app.control.inspect(timeout=0.5).ping():
        break
    time.sleep(0.1)
else:
    raise RuntimeError('Worker did not reply to ping.')
call_command('check_celery', '--timeout', '1', '--json')
state = queue_email('CubeGears worker test', 'Real Redis task execution.', ['worker-test@example.test'],
                    action_url='https://example.test/setup-password?test=1', action_label='Set password')
assert state['status'] == 'queued', state
for attempt in range(100):
    files = list(Path(settings.EMAIL_FILE_PATH).glob('*'))
    if files and 'Real Redis task execution.' in files[0].read_text():
        break
    time.sleep(0.1)
else:
    raise RuntimeError('Worker did not write the test email.')
message = files[0].read_text()
assert 'worker-test@example.test' in message
assert 'Set password' in message
assert 'text/html' in message
result = celery_app.send_task('credits.renew_due_features', ignore_result=False).get(timeout=10)
assert result == 'disabled', result
print('PASS: configured app, Redis health, worker registration, queued HTML email execution, billing task disabled.')
'''
                subprocess.run([sys.executable, '-c', producer], cwd=ROOT, env=env, check=True, timeout=60)
            except Exception:
                worker_log.flush()
                worker_log.seek(0)
                # These logs contain only isolated test settings and dummy mail.
                print(worker_log.read()[-6000:], file=sys.stderr)
                raise
            finally:
                stop(worker)
                stop(redis_process)


if __name__ == '__main__':
    main()
