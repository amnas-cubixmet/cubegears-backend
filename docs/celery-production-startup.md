# CubeGears: automatic Celery startup in production

The existing `config/celery.py` and `config/settings.py` already configure the
Celery app and (opt-in) periodic schedules. No Celery process should be launched
by Django `AppConfig.ready()`, Gunicorn/WSGI startup, migrations, or a build
hook; it can duplicate billing jobs and processes can be killed.

## Configure once

On a host with supported long-running background services (Railway or VPS),
provision Redis and set these environment values **privately for Django, Worker,
and Beat**:

```env
DJANGO_TIME_ZONE=Asia/Kolkata
CELERY_BROKER_URL=redis://<redis-host>:6379/0
CELERY_RESULT_BACKEND=redis://<redis-host>:6379/1
CELERY_TASK_ALWAYS_EAGER=False
# Leave actual billing disabled until after staging verification:
CREDIT_BILLING_CELERY_ENABLED=False
```

Use the correct provider URL; `127.0.0.1` in a separate container does not refer
to the Redis service. Never commit production URLs or credentials.

## Create services from the same GitHub repository

| Service | Start command |
| --- | --- |
| Django Web | `gunicorn config.wsgi:application --bind 0.0.0.0:$PORT` |
| Celery Worker | `bash scripts/start_celery.sh worker` |
| Celery Beat | `bash scripts/start_celery.sh beat` |

Configure **one** Beat service. The host's process supervisor should start
the services at deployment and restart failed services. A build stage alone
cannot keep a background process alive. The launch script intentionally leaves
billing feature flags unchanged.

The worker concurrency defaults to 1 because the repository currently uses
SQLite. If services are in different containers with separate local SQLite
files, business data will be inconsistent. Use shared durable storage with
careful locking or migrate to an appropriate shared production DB before
running multi-service billing.

## Verify

```bash
python manage.py check
python manage.py shell -c "from django.conf import settings; print(settings.CELERY_BEAT_SCHEDULE)"
python -m celery -A config inspect ping
python -m celery -A config inspect registered
```

Check the Worker logs and Beat logs. A running Beat with
`CELERY_BEAT_SCHEDULE={}` will not enqueue periodic jobs. Enable individual
billing or email flags only when the corresponding services, broker, and
production rules have been validated in staging.

## PythonAnywhere limitation

PythonAnywhere's own help documentation currently says Celery is not
supported. Its Free plan has no Always-on tasks. Do not paste Worker or Beat
commands into the Django WSGI file or assume these scripts bypass host
restrictions. For PythonAnywhere, consider scheduled Django management
commands where account features permit, or deploy the Celery workers to
another supported host.

References:
- https://help.pythonanywhere.com/pages/AsyncInWebApps
- https://help.pythonanywhere.com/pages/AlwaysOnTasks
