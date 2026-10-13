# CubixGear production rollout — SQLite-only Django backend

> SQLite is the selected database. This checklist does not guarantee production readiness.

## Architecture and hosting

- **Company Panel (React/Vite):** deploy to Vercel with `VITE_API_URL=https://api.example.com/api/v1`.
- **Django backend:** deploy to a VPS or service providing a persistent writable disk, as a *single application instance*.
- **Database:** SQLite at `db.sqlite3` locally or an absolute `SQLITE_DATABASE_PATH` on a persistent disk. No Prisma, `DATABASE_URL`, `POSTGRES_URL`, or `PRISMA_DATABASE_URL` is used.
- **Email + scheduled tasks:** run the email provider and worker/beat dependencies configured for your environment.

**Never run a writable production SQLite database on Vercel Functions**. Their file storage is ephemeral, and concurrent function instances do not share a durable SQLite file. The backend detects `VERCEL=1` in production and refuses to start to avoid customer and invoice data loss.

## Production environment

Set the following values in your backend host's private environment settings:

```dotenv
DJANGO_DEBUG=False
DJANGO_SECRET_KEY=<independent-random-secret-32+-bytes>
JWT_SIGNING_KEY=<independent-random-jwt-key-32+-bytes>
DJANGO_ALLOWED_HOSTS=api.example.com
SQLITE_DATABASE_PATH=/var/lib/cubixgear/db.sqlite3
CORS_ALLOWED_ORIGINS=https://app.example.com
CSRF_TRUSTED_ORIGINS=https://app.example.com
FRONTEND_URL=https://app.example.com
SECURE_SSL_REDIRECT=True
SECURE_HSTS_SECONDS=3600
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=<smtp-host>
EMAIL_PORT=587
EMAIL_HOST_USER=<smtp-username>
EMAIL_HOST_PASSWORD=<smtp-secret>
EMAIL_USE_TLS=True
DEFAULT_FROM_EMAIL=accounts@example.com
CELERY_BROKER_URL=<redis-broker>
CELERY_RESULT_BACKEND=<redis-result>
CELERY_TASK_ALWAYS_EAGER=False
```

Use a directory mounted from durable storage, writable by the Django OS user. Store the database **outside** the Git checkout/deployment release folder, so application upgrades do not overwrite it. Avoid a network filesystem without reliable SQLite locking.

## Installation and migrations

```bash
python -m pip install -r requirements.txt
python manage.py check --deploy
python manage.py migrate --noinput
python manage.py collectstatic --noinput
gunicorn config.wsgi:application --bind 0.0.0.0:8000
```

Configure reverse-proxy HTTPS + static file serving. Keep **private job media** in separate persistent storage with authorization checks. SQLite does not store uploaded media files by itself.

## Safe SQLite backup and restore

- Back up SQLite periodically using the SQLite Online Backup API (`sqlite3.Connection.backup`) or a backup service that safely handles open database connections. Do **not** rely on copying an in-use `db.sqlite3` file.
- Encrypt backups, store at least one copy off-server, and regularly verify restoration.
- Before schema migrations, take a tested backup; keep a migration rollback plan.
- Start with a single Django writer instance. SQLite serializes writes; monitor for `database is locked` errors as traffic increases.
- If SQLite becomes a bottleneck, migrate to a durable managed database as a separate future project, not as part of this rollout.

## RBAC and launch smoke tests

- Public workshop signup creates Company, Head Office, and a tenant `SUPER_ADMIN` owner, **not** a platform superuser.
- Email setup flow, login, JWT refresh, logout, password recovery and company separation work end-to-end.
- Permissions are enforced on the backend, not only by hiding frontend routes.
- Jobs, stock, invoices, GST estimates/e-way bills and daily wages must be tested with real permission combinations and distinct company accounts.
- Verify database durability across app restarts and redeployments, as well as backup and restore.
