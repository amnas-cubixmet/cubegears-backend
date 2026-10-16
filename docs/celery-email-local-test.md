# CubeGears: Local Celery email smoke test (Windows / PowerShell)

This test separates queue delivery from real SMTP delivery. Start with the
console email backend: **no real email will be sent**. Celery Beat is not
needed for individual outbound email tasks, and credit billing must remain
disabled for this test.

## 1. Install optional dependencies

From the activated Django backend virtual environment:

```powershell
pip install "celery[redis]>=5.6,<5.8"
python manage.py check
python manage.py test apps.accounts.test_celery_email_delivery apps.accounts.test_rbac_email.DirectEmailTests --noinput
```

The CI tests use mocks / Django's in-memory backend. Passing tests here
does **not** prove SMTP delivery or that Redis is reachable.

## 2. Start Redis

With Docker Desktop running:

```powershell
docker run -d --name cubegears-mail-redis -p 6379:6379 redis:7-alpine
docker exec cubegears-mail-redis redis-cli ping
```

Expected: `PONG`. If the container already exists, use
`docker start cubegears-mail-redis`. If port 6379 is in use,
use a working existing Redis instance or adjust the port in both places.

## 3. Configure backend .env

```env
EMAIL_CELERY_ENABLED=True
CELERY_BROKER_URL=redis://127.0.0.1:6379/0
CREDIT_BILLING_CELERY_ENABLED=False

EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
DEFAULT_FROM_EMAIL=CubeGears <no-reply@example.test>
```

Use only backend .env. Do not put SMTP passwords into React/Vite variables or
commit any secrets. Fix existing malformed .env statements before proceeding.

## 4. Worker (separate terminal)

```powershell
celery -A config.celery:app worker --loglevel=info --pool=solo
```

Windows requires `--pool=solo` for this local workflow. The startup
output should register `apps.accounts.tasks.send_email_task` and connect to
`redis://127.0.0.1:6379/0`, not `amqp://guest@127.0.0.1:5672//`.

## 5. Queue a plain notification (separate terminal)

```powershell
python manage.py shell -c "from apps.accounts.email import queue_email; print(queue_email('CubeGears Celery Smoke Test', 'Hello from the Redis worker', ['test@example.com']))"
```

Expected output includes `{'status': 'queued'}`.
The worker terminal should then show the task being received and the rendered
email in console mode. A queued status alone does not prove delivery.

You can also queue the task directly for worker diagnosis:

```powershell
python manage.py shell -c "from config.celery import app; from apps.accounts.tasks import send_email_task; print(send_email_task.apply_async(args=['Direct Task Test','Hello',['test@example.com']]).id)"
```

## 6. Actual inbox test (only with permission to use that mailbox)

For real delivery, replace the console mail backend with your SMTP provider
settings in backend .env, then restart **both** Django and Celery:

```env
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_HOST_USER=your-address@gmail.com
EMAIL_HOST_PASSWORD=your-google-app-password
EMAIL_USE_TLS=True
EMAIL_USE_SSL=False
DEFAULT_FROM_EMAIL=your-address@gmail.com
```

Use a real Gmail App Password only if the Google account supports it.
Send to an inbox you control and check its Spam folder. The SMTP provider
accepting a message is not proof that it reached an inbox.

## Caveats

- Password reset, setup-password, verification and magic-link emails that
  include `action_url` intentionally stay synchronous in the current app.
- If Redis is unavailable, `queue_email` attempts direct delivery. With
  the console backend this prints locally, so verify the worker log before
  declaring Celery healthy.
- Do not enable `CREDIT_BILLING_CELERY_ENABLED` merely to test email: it can
  schedule actual billing tasks. You do not need Celery Beat for one email.
- Confirm a worker is running before trusting queued results.
