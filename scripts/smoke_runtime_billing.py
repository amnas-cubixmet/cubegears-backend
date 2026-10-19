"""Exercise runtime billing with private Redis, a worker, Beat and a temporary DB.

Requires redis-server on PATH or --redis-server. No real tenant, external broker,
SMTP server or wallet is contacted. All processes/data are removed afterwards.
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

from smoke_celery_worker import stop

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--redis-server', default=shutil.which('redis-server'))
    options = parser.parse_args()
    if not options.redis_server:
        parser.error('Install redis-server or pass --redis-server /path/to/redis-server.')
    with tempfile.TemporaryDirectory(prefix='cubegears-billing-') as folder:
        directory = Path(folder)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        broker = f'redis://127.0.0.1:{port}/0'
        source = '\n'.join([
            'from config.settings import *',
            f'CELERY_BROKER_URL = {broker!r}',
            'CREDIT_BILLING_CELERY_ENABLED = True',
            'STORAGE_MONTHLY_METERED_ENABLED = True',
            'CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED = False',
            'EMAIL_CELERY_ENABLED = False',
            'BILLING_EMAIL_ALERTS_ENABLED = False',
            "CELERY_BEAT_SCHEDULE = {'runtime': {'task':'credits.run_configured_billing', 'schedule':1.0}}",
            f'DATABASES = {{"default": {{"ENGINE":"django.db.backends.sqlite3", "NAME":{str(directory / "test.sqlite3")!r}}}}}',
            f'MEDIA_ROOT = {str(directory / "media")!r}',
        ])
        (directory / 'billing_smoke_settings.py').write_text(source)
        env = {**os.environ, 'DJANGO_SETTINGS_MODULE':'billing_smoke_settings',
               'PYTHONPATH':str(directory)+os.pathsep+str(ROOT),
               'CREDIT_BILLING_CELERY_ENABLED':'False'}
        # The import starts with flags off, then the isolated settings enable
        # only the intended schedule. No ambient production broker is used.
        subprocess.run([sys.executable, 'manage.py', 'migrate', '--noinput'], cwd=ROOT,
                       env=env, check=True, stdout=subprocess.DEVNULL)
        fixture = '''
import django
django.setup()
from datetime import timedelta
from django.utils import timezone
from django.core.files.base import ContentFile
from apps.companies.models import Company
from apps.saas.models import MediaFile
from apps.credits.models import BillingCyclePolicy, StorageCreditPolicy, StorageMeterCursor
BillingCyclePolicy.objects.create(enabled=True, event_tracking_enabled=True,
    storage_tracking_interval_seconds=60, calculation_interval_seconds=60,
    rate_duration_seconds=60, effective_month=timezone.localdate().strftime('%Y-%m'))
StorageCreditPolicy.objects.create(is_active=True, credits_per_gb=1000000)
company = Company.objects.create(name='Isolated billing smoke', slug='billing-smoke')
MediaFile.objects.create(company=company, name='fixture.bin', file=ContentFile(b'smoke' * 1024, name='fixture.bin'))
# Synthetic already-observed interval in a throwaway fixture, not tenant history.
StorageMeterCursor.objects.filter(company=company).update(sampled_at=timezone.now()-timedelta(seconds=120))
'''
        subprocess.run([sys.executable, '-c', fixture], cwd=ROOT, env=env, check=True)
        processes = []
        with (directory/'redis.log').open('w+') as redis_log, (directory/'worker.log').open('w+') as worker_log, (directory/'beat.log').open('w+') as beat_log:
            try:
                processes.append(subprocess.Popen([options.redis_server, '--bind','127.0.0.1',
                    '--port',str(port),'--save','','--appendonly','no','--dir',folder],
                    stdout=redis_log, stderr=subprocess.STDOUT, env=env))
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
                processes.append(subprocess.Popen([sys.executable,'-m','celery','-A','config','worker',
                    '--pool=solo','--concurrency=1','--loglevel=info','--without-gossip','--without-mingle'],
                    cwd=ROOT, env=env, stdout=worker_log, stderr=subprocess.STDOUT))
                processes.append(subprocess.Popen([sys.executable,'-m','celery','-A','config','beat',
                    '--loglevel=info','--schedule',str(directory/'beat-schedule')],
                    cwd=ROOT, env=env, stdout=beat_log, stderr=subprocess.STDOUT))
                verify = '''
import django
django.setup()
import time
from apps.credits.models import StorageMeterPeriod, CreditLedger
for attempt in range(90):
    row = StorageMeterPeriod.objects.first()
    if row and row.accrued_credits > 0:
        assert not CreditLedger.objects.exists(), 'Open month must not debit a wallet'
        print('PASS: Beat dispatched billing; worker accrued the configured storage interval; no wallet debit.')
        break
    time.sleep(1)
else:
    raise RuntimeError('Beat/worker did not accrue the test interval within 90 seconds.')
'''
                subprocess.run([sys.executable,'-c',verify], cwd=ROOT, env=env, check=True)
            except Exception:
                for log in (worker_log, beat_log):
                    log.flush()
                    log.seek(0)
                    print(log.read()[-4000:], file=sys.stderr)
                raise
            finally:
                for process in reversed(processes):
                    stop(process)


if __name__ == '__main__':
    main()
