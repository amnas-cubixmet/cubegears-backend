# CubixGear production rollout (Django + PostgreSQL)

> This is a deployment checklist, not a claim that the project is production-certified.

## 1. Rotate exposed credentials

If a Prisma PostgreSQL connection URL has appeared in chat, a ticket, a repository, or logs, rotate/revoke its database password/token in the provider console first. Update only deployment secret settings with the **new** connection value. Do not paste connection URLs into GitHub code, frontend `VITE_*` values, or public logs.

The backend already uses `dj_database_url` and `psycopg`. It reads the single `DATABASE_URL` variable; duplicate `POSTGRES_URL` and `PRISMA_DATABASE_URL` values are not required for Django.

## 2. Backend environment

Provide these on the backend hosting platform (example names only; never commit real values):

```dotenv
DJANGO_DEBUG=False
DJANGO_SECRET_KEY=<random-32+-byte-secret>
JWT_SIGNING_KEY=<independent-random-32+-byte-secret>
DJANGO_ALLOWED_HOSTS=api.example.com
DATABASE_URL=<ROTATED-PRISMA-POSTGRESQL-URL>
CORS_ALLOWED_ORIGINS=https://app.example.com
CSRF_TRUSTED_ORIGINS=https://app.example.com
FRONTEND_URL=https://app.example.com
SECURE_SSL_REDIRECT=True
SECURE_HSTS_SECONDS=3600
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=<SMTP-HOST>
EMAIL_PORT=587
EMAIL_HOST_USER=<SMTP-USER>
EMAIL_HOST_PASSWORD=<SMTP-SECRET>
EMAIL_USE_TLS=True
DEFAULT_FROM_EMAIL=accounts@example.com
CELERY_BROKER_URL=<REDIS-BROKER-URL>
CELERY_RESULT_BACKEND=<REDIS-RESULT-URL>
CELERY_TASK_ALWAYS_EAGER=False
```

Choose valid real hostnames and deploy behind a trusted TLS-terminating reverse proxy. Increase HSTS only after HTTPS works on all production paths.

The frontend requires `VITE_API_URL=https://api.example.com/api/v1` set in its deployment build environment. Frontend builds intentionally fail without this variable. No database secret belongs in the frontend.

## 3. Deploy and validate

```bash
pip install -r requirements.txt
python manage.py check --deploy
python manage.py migrate --noinput
python manage.py collectstatic --noinput
gunicorn config.wsgi:application --bind 0.0.0.0:8000
```

Run a separate Redis-backed worker:

```bash
celery -A config worker --loglevel=info
```

Run Celery Beat for scheduled attendance tasks, as a separate process:

```bash
celery -A config beat --loglevel=info
```

Ensure a static file server/CDN serves collected static files; Django alone does not serve those in production. Secure private job media in persistent object storage with access checks (not public /media/). Use encrypted, tested backups and database migration rollback procedures.

## 4. Onboarding & access verification

- Public signup creates a new company, Head Office, and a **company** `SUPER_ADMIN` role.
- The first user is **not** a Django/platform `is_superuser` or `is_staff` user.
- Email password setup must complete through a live SMTP + Celery worker.
- The owner can manage only their own company's roles, jobs and branches.
- A tenant role from another company never authorizes access.
- Inactive tenants/roles lose access, and custom roles cannot receive wildcard `*` permissions.
- Test role changes after a fresh login; never rely solely on hidden frontend navigation.

## 5. Remaining release blockers

The feature branches do not assert full production certification. Before merging, audit cross-tenant foreign-key writes and every custom API action, implement enforced branch-level role scopes if needed, review file/media security, verify signup + mail delivery + token refresh against the live deployment, add monitoring and rate limiting using a shared cache/proxy, and conduct load, backup/restore and security testing.

The two pull requests remain drafts until their validation and deployment dependencies are approved.
