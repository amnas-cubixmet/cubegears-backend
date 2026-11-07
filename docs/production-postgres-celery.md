# CubeGears — PostgreSQL / Django / Celery Production Setup

This is deployment configuration, **NOT a live database migration**. Existing
SQLite data is preserved until a separate verified migration.

## Production architecture

| Service | Separate process | Purpose |
|---|---|---|
| postgres:16 | PostgreSQL server | Persistent workshop data, invoices, payments and audit ledger |
| django-web | Gunicorn + Django | HTTP API and platform admin |
| redis:7 | Redis server | Private Celery queue |
| celery-worker | Celery worker | Background tasks; shares Django code and PostgreSQL |
| celery-beat (optional) | One Beat scheduler | Periodic jobs only after enabling audited flags |
| company-panel | Vite / Vercel | Browser UI; requests HTTPS Django API |

Do not use npm for Django; npm is used only for the React/Vite Company Panel.
Django and Celery both install requirements.txt, but run as separate services.

## Production ENV: example.txt -> private .env

The backend includes **ENV.production.example.txt** and
**.env.production.example**. They have placeholders, not live secrets.
In Windows PowerShell, from the backend folder:

~~~powershell
Copy-Item .env.production.example .env
notepad .env
python -c "import secrets; print(secrets.token_urlsafe(48))"
~~~

Generate strong UNIQUE Django and JWT keys and fill all blank production
variables privately: DJANGO_SECRET_KEY, JWT_SIGNING_KEY, POSTGRES_PASSWORD,
REDIS_PASSWORD, CELERY_BROKER_URL and CELERY_RESULT_BACKEND.

For Docker Compose, POSTGRES_HOST=postgres and POSTGRES_SSLMODE=disable work
**only on the isolated container network**. The Redis broker URL must include
the URL-encoded Redis password, for example:
redis://:URL_ENCODED_PASSWORD@redis:6379/0. Use /1 for result backend.
Never expose ports 5432/6379 publicly.

For managed PostgreSQL, use **either** DATABASE_URL with SSL enabled **or**
POSTGRES_HOST/POSTGRES_DB/POSTGRES_USER/POSTGRES_PASSWORD. Never both.
Set real HTTPS DJANGO_ALLOWED_HOSTS, FRONTEND_URL, CORS_ALLOWED_ORIGINS,
CSRF_TRUSTED_ORIGINS and CUSTOMER_PORTAL_BASE_URL.

### Fresh EMPTY production database (Linux VPS)

~~~bash
docker compose -f compose.production.yml config --quiet
docker compose -f compose.production.yml up -d --build postgres redis
docker compose -f compose.production.yml build django-web
docker compose -f compose.production.yml run --rm django-web python manage.py check
docker compose -f compose.production.yml run --rm django-web python manage.py migrate --noinput
docker compose -f compose.production.yml run --rm django-web python scripts/check_production_config.py --connect-db --connect-redis
docker compose -f compose.production.yml up -d django-web celery-worker
docker compose -f compose.production.yml logs --tail=100 django-web celery-worker
~~~

Gunicorn is bound to 127.0.0.1:8000 on the VPS host; configure an HTTPS
reverse proxy in front of it. It must forward correctly vetted X-Forwarded-Proto.
Django serves collected static files using WhiteNoise. Media and private job
files are on persistent Docker volumes and require separate backups.

Enable one Celery Beat **only after reviewing scheduled jobs**:

~~~bash
docker compose --profile scheduler -f compose.production.yml up -d celery-beat
~~~

Keep CREDIT_BILLING_CELERY_ENABLED=False, EMAIL_CELERY_ENABLED=False,
BILLING_EMAIL_ALERTS_ENABLED=False and ATTENDANCE_AUTO_CHECKOUT_ENABLED=False
until each workflow is individually verified.

### Important: preserve EXISTING local SQLite data

**A database ENV switch does not migrate any data.** Do not point production
to an empty PostgreSQL server until workshop data is copied and audited.

1. Schedule maintenance; stop old Django and Celery writers. Back up
   SQLite with a non-destructive, integrity-checked helper:

~~~powershell
python scripts/backup_sqlite.py --source db.sqlite3 --output db-backups/pre-postgres.sqlite3
~~~

   Back up both public and private media directories. Keep files outside Git.
2. With the OLD SQLite ENV active, export data:

~~~powershell
New-Item -ItemType Directory -Force db-backups
python manage.py dumpdata --natural-foreign --natural-primary --exclude contenttypes --exclude auth.permission --exclude sessions --exclude token_blacklist --output db-backups/cubegears-staging.json
~~~

3. In a CLEAN staging PostgreSQL database, run migrations, then test importing
   the file with python manage.py loaddata db-backups/cubegears-staging.json.
   Validate foreign keys, sequences, custom models and content types.
4. Reconcile all company, vehicle, staff, supplier and customer counts;
   job cards, invoice totals, balances, payment ledger entries, credit and
   inventory stock. Verify media uploads and authentication.
5. Only then run the final maintenance-window cutover. Keep the original
   database read-only for rollback. Never silently reset or overwrite it.

On an EXISTING SQLite production host (for example PythonAnywhere), deploying
a PostgreSQL-required version before provisioning PostgreSQL would interrupt
the backend. Either postpone deployment or temporarily configure
ALLOW_LEGACY_PRODUCTION_SQLITE=True in the existing private .env. This
explicit compatibility switch must be removed after migration.

Do not generate schema on production via makemigrations; review schema changes
in source control and deploy only vetted migrations using migrate --noinput.

### Frontend npm, Vite and Vercel

From the company-panel folder on Windows:

~~~powershell
Copy-Item .env.production.example .env.production
notepad .env.production
npm ci
npm run check:production-env
npm run build:production
~~~

Replace the example HTTPS API URL with the real deployed backend ending in
/api/v1. Set VITE_USE_MOCK_API=false. VITE_* variables are PUBLIC;
never include passwords or API secrets.

In Vercel Settings -> Environment Variables, configure production values
and use the Build Command npm run build:production. The build fails if the
API is missing, localhost, HTTP, placeholder or mock. Configure the domain
before trying to activate this branch.

### Local Windows development (separate processes)

Copy backend .env.example to backend/.env (local SQLite stays untouched).
Use four separate PowerShell terminals:

~~~powershell
# Terminal 1: Redis (Docker)
docker run --rm -p 127.0.0.1:6379:6379 redis:7-alpine
# Terminal 2: Django
python manage.py runserver 127.0.0.1:8000
# Terminal 3: Celery Worker (Windows solo pool)
celery -A config worker --loglevel=INFO --pool=solo
# Terminal 4: Celery Beat (only when scheduling is enabled)
celery -A config beat --loglevel=INFO
~~~

Frontend from company-panel: npm ci; npm run dev.
Production should use Gunicorn, NOT runserver.

### Security, recovery, monitoring

Old committed example files previously contained credential-like values.
Rotate active SMTP, payment gateway/webhook and Django signing keys.
Removing credentials from the current file does NOT remove them from old
Git history. Secret rotation can invalidate sessions; plan accordingly.

Back up PostgreSQL using encrypted pg_dump -Fc and verify restores.
Back up persistent media volumes separately. Review finance and supplier
payments on a staging instance, and make sure only ONE Beat schedules
periodic jobs. Monitor web, worker, Beat and DB independently.

Production preflight: python manage.py check --deploy;
python scripts/check_production_config.py --connect-db --connect-redis.
These commands do not expose secret values.

**Status**: This repository does not provision a cloud Postgres instance,
migrate existing customer data or configure real credentials automatically.


## Part 02: separate production dependencies

- `GET /api/v1/health/` checks web process liveness without contacting the database.
- `GET /api/v1/ready/` checks PostgreSQL connectivity with a real `SELECT 1`. It responds with HTTP 503 when unavailable.
- Set `REQUIRE_REDIS_READINESS=True` to additionally require Redis readiness for web traffic. This defaults to False because the web process may remain useful during a noncritical worker outage.
- `python manage.py verify_production_foundation --require-redis` checks database and Redis plus the writable persistent public/private upload volumes. The command does not mutate customer or workshop records.
- `POSTGRES_CONNECT_TIMEOUT_SECONDS=10` limits connection attempts. When `DATABASE_URL` already supplies a PostgreSQL `sslmode`, that choice is preserved unless `POSTGRES_SSLMODE` is explicitly set in the environment.
- `PRIVATE_JOB_MEDIA_ROOT` must not be nested inside public `DJANGO_MEDIA_ROOT` or `STATIC_ROOT`. Invalid paths fail at startup rather than publishing private inspection media.
- Configure all environment values on backend services only; never put secrets in Vite `VITE_*` variables. Ensure persistent volumes exist before running the preflight command.
