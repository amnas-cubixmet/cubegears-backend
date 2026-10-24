# PythonAnywhere deployment: supported non-Celery operation

This is the live implementation guide for `config/settings.py` and
`apps/accounts/email.py`. PythonAnywhere does not officially support
Celery/Beat; do not launch a background worker in WSGI or `AppConfig.ready()`.
Use **direct SMTP** for account emails and, if your plan permits it,
a PythonAnywhere **Scheduled Task** for attendance auto-checkout.

## Critical: preserve production database, keys and uploaded photos

Before updating a checkout that contains historical tracked `private_job_media/`,
log into PythonAnywhere and make a safe standalone copy:

```bash
cd /home/cardflow/cubegears-backend
mkdir -p /home/cardflow/private_job_media
cp -an private_job_media/. /home/cardflow/private_job_media/
# Verify copies before changing .env or pulling the cleanup PR.
find /home/cardflow/private_job_media -type f | wc -l
```

Set `PRIVATE_JOB_MEDIA_ROOT=/home/cardflow/private_job_media` in the
private `.env`. The original tracked photo is **removed from the current Git
tree** by the security cleanup, so `git pull` may delete the checkout copy.
Its copies in Git **history still exist**; coordinate a separate history purge
with repository collaborators if the photo contained private information.
Do not publish private files or turn on public static routes for these images.

Back up the live SQLite database with the SQLite online backup API (do not
blindly copy `db.sqlite3` while Django is writing). Do not change the
existing `DJANGO_SECRET_KEY`, `JWT_SIGNING_KEY`, `SQLITE_DATABASE_PATH`,
or payment keys as part of this update.

## Setup and configuration

```bash
cd /home/cardflow/cubegears-backend
source /home/cardflow/.virtualenvs/cubegears-env/bin/activate
python -m pip install -r requirements.txt
python manage.py check
python manage.py migrate --plan
```

Recommended private `.env` values for PythonAnywhere (replace all example
hosts and SMTP values; keep existing signing keys and credentials):

```dotenv
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=cardflow.pythonanywhere.com
DJANGO_TIME_ZONE=Asia/Kolkata
SQLITE_DATABASE_PATH=/home/cardflow/cubegears-backend/db.sqlite3
PRIVATE_JOB_MEDIA_ROOT=/home/cardflow/private_job_media
DJANGO_MEDIA_ROOT=/home/cardflow/cubegears-backend/media
FRONTEND_URL=https://YOUR-REAL-FRONTEND.example
CORS_ALLOWED_ORIGINS=https://YOUR-REAL-FRONTEND.example
CSRF_TRUSTED_ORIGINS=https://cardflow.pythonanywhere.com
ACCOUNT_EMAIL_DELIVERY_MODE=smtp
EMAIL_CELERY_ENABLED=False
CREDIT_BILLING_CELERY_ENABLED=False
ATTENDANCE_AUTO_CHECKOUT_ENABLED=False
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=YOUR-REACHABLE-SMTP-SERVER
EMAIL_PORT=587
EMAIL_HOST_USER=YOUR-ACCOUNT
EMAIL_HOST_PASSWORD=YOUR-PRIVATE-APP-PASSWORD
EMAIL_USE_TLS=True
EMAIL_USE_SSL=False
DEFAULT_FROM_EMAIL=verified-sender@your-domain.example
```

`ACCOUNT_EMAIL_DELIVERY_MODE=smtp` sends signup, reset, login links
synchronously through Django's SMTP backend **after the database transaction
commits**. SMTP delivery is slower than Celery and can fail if the provider
is blocked by your PythonAnywhere plan; no secret is logged and public
recovery endpoints remain non-enumerating. This mode is opt-in and has **no
silent fallback** from a failed Celery dispatch.

Do not use Django's console backend for real account links.
Check the provider's outbound access policies for your PythonAnywhere plan.

## Verify email from an authorized mailbox

```bash
python manage.py check_email_delivery
python manage.py check_email_delivery --to you@your-owned-domain.example
```

The `--to` form sends one harmless test only when explicitly requested.
Acceptance by SMTP is not proof of inbox delivery. Review inbox/spam.
Do not print `.env`, passwords or generated account action links in tickets.

## Schedule automatic attendance without Celery

The repository includes `python manage.py process_auto_checkout --run`.
**It modifies attendance records** when auto or hybrid shift policies are due.
Keep the opt-in off until attendance rules are reviewed in staging.

On a PythonAnywhere plan with Scheduled Tasks, set:

```dotenv
ATTENDANCE_AUTO_CHECKOUT_ENABLED=True
```

Then schedule the task (in PythonAnywhere **Tasks**, at the cadence your plan
supports) with:

```bash
cd /home/cardflow/cubegears-backend && /home/cardflow/.virtualenvs/cubegears-env/bin/python manage.py process_auto_checkout --run
```

This command runs on the host's existing persistent SQLite database
**without Redis**. A daily-only task is NOT equivalent to five-minute
auto-checkout; it may run much later than shift end. Never use both this
scheduled task and a Celery Beat checkout task simultaneously.

For a safe configuration-only check:

```bash
python manage.py process_auto_checkout
```

## Django web deployment

PythonAnywhere Web tab virtualenv path:
`/home/cardflow/.virtualenvs/cubegears-env`

WSGI entry: `/var/www/cardflow_pythonanywhere_com_wsgi.py`:

```python
import os
import sys
project_path = "/home/cardflow/cubegears-backend"
if project_path not in sys.path:
    sys.path.insert(0, project_path)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()
```

The settings module reads the private `.env`. Run
`python manage.py check --deploy`, check the existing migrations
(`python manage.py migrate --plan`), then `collectstatic --noinput`,
configure PythonAnywhere static mapping for `/static/` and reload via
the **Web** tab. Test authentication, password reset, company access and
billing safely in staging.

## Celery-compatible hosting

Use `ACCOUNT_EMAIL_DELIVERY_MODE=celery` and
`EMAIL_CELERY_ENABLED=True` **only** after Redis and an independent worker
are running. Enable `ATTENDANCE_AUTO_CHECKOUT_ENABLED=True` when a single
Beat scheduler is running; the Beat job runs every five minutes.
Do not enable `CREDIT_BILLING_CELERY_ENABLED` until billing policies and
existing tenant entitlement migrations have been audited.

This project uses SQLite. A Worker or Beat in a different container will
not share Django's local SQLite file automatically. Do not point an external
worker at a separate copy of the database or attempt cross-container billing
without a supported shared durable database design.
