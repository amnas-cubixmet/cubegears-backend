# Celery worker setup and verification

Django now imports the configured `config.celery_app`, so shared tasks bind to
CubeGears rather than Celery's unconfigured default app. This import does not
start a worker, connect to Redis, send mail or enable billing.

## Start email processing

Install backend dependencies (`pip install -r requirements.txt`) and run Redis
on the worker's network. Set these in the private backend environment used by
**both Django and the worker**:

```dotenv
CELERY_BROKER_URL=redis://127.0.0.1:6379/0
EMAIL_CELERY_ENABLED=True
CREDIT_BILLING_CELERY_ENABLED=False
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=your-smtp-host
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_USE_SSL=False
EMAIL_HOST_USER=your-smtp-user
EMAIL_HOST_PASSWORD=your-private-smtp-password
DEFAULT_FROM_EMAIL=your-authorized-sender
```

Restart Django and start the worker in a separate terminal from the backend
repository, with its virtual environment active:

```bash
celery -A config worker --loglevel=info
python manage.py check_celery --json
python manage.py check_email_delivery
```

For the existing Windows local workflow use
`celery -A config worker --pool=solo --loglevel=info`.
`check_celery` verifies broker connectivity, worker replies and registration of
email and configured Beat tasks. It exits nonzero if a check fails, does not
send email/billing tasks, and does not print the broker URL/password.
`check_email_delivery` checks dispatch/provider configuration; it does not
prove inbox receipt. Queued means Redis accepted a message, not that SMTP sent it.

Use the same application version, environment, task queue routing and Redis
broker on Django and every worker. Run the worker under your deployment's
process supervisor with automatic restart; a terminal session closing stops an
unsupervised worker. WSGI alone does not run Celery workers.

## Scheduled billing

Email does not require Beat. For configured billing policies set
`CREDIT_BILLING_CELERY_ENABLED=True` in the worker/Django/Beat environment and
restart all three. Then start **one** scheduler:

```bash
celery -A config beat --loglevel=info
python manage.py check_celery --json
```

Existing schedule selection follows the enabled calendar/monthly/storage flags
and database billing policies. Do not run duplicate Beat or legacy cron jobs.
Prices, due dates, policies and existing balance safeguards still apply.
This change does not activate any company feature or charge a wallet by itself.

## Repeatable real-worker test

With `redis-server` installed:

```bash
python scripts/smoke_celery_worker.py
python manage.py test apps.accounts.test_celery_health apps.accounts.test_celery_email_delivery apps.credits.test_celery_billing_tasks --noinput
```

The smoke script starts a private Redis on an ephemeral local port and a separate
Celery worker, sends a dummy HTML email through `queue_email`, and verifies that
the worker writes it to a temporary file mailbox. It also runs a disabled billing
task through Redis and checks its returned result. It does not use SMTP, contact
an existing broker, flush databases, migrate a tenant database or debit balances.
Both processes and all temporary files are removed afterwards. CI runs this test.
A standalone Redis binary can be supplied with `--redis-server /path/to/redis-server`.
