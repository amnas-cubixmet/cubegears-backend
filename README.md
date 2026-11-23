# CubixGear Backend

Production-oriented Django REST backend for the CubixGear workshop SaaS and company panel.

## Implemented modules

- Accounts: custom user, JWT login/logout/refresh, /auth/me, password change, forgot/reset password, magic link
- Company/SaaS: company profile, GST, address, currency, plan, billing summary, storage, files, document templates, settings, security events
- Branches and roles/permissions
- Customers: CRUD, duplicate check, vehicles, service/jobs history, invoices, payments, outstanding, reminders/activity
- Vehicles: CRUD and service history
- Services: service catalogue
- Job cards: complaints, inspection, estimates, labour/work JSON workflow, parts, photos, activity/history, QC and exact status workflow
- Stock: items, categories, suppliers, SKU/barcode, movements, purchase orders, transfers, adjustments/audit, low-stock and job-card deductions
- Billing: invoices, estimates/quotations, GST, HSN/SAC, payments, E-Way Bill and expenses
- Staff: employees, teams, shifts, skills, documents and login invitations
- Attendance: clock in/out, history, leave, overtime, holidays, rules, corrections and manager compatibility APIs
- Payroll: salary setup, advances, incentives, overtime inputs, payroll runs, payslips and payments
- Dashboard, reports and notifications

## Job workflow

```text
New
→ Inspection
→ Estimate Pending
→ Approved
→ In Progress
→ Waiting for Parts
→ QC
→ Ready for Delivery
→ Delivered
```

## Local setup

### One-command local development (Windows)

After installing Python dependencies and creating a **local** `.env` (not the
production PythonAnywhere values), start Django, Celery Worker, Celery Beat and
Redis together:

```powershell
# In the backend repository; first-time setup only
py -3 -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt

# Every development session
.\start-all.bat
```

The launcher uses `venv` or `.venv`, runs `python manage.py check`, then
starts Django (`http://127.0.0.1:8000`), Celery (Windows-safe solo pool) and
Beat. It detects a running Redis instance, or starts/reuses the
`cubegears-redis` Docker container when Docker Desktop is available.
Press **Ctrl+C** in the launcher to stop all three processes. If the launcher
started the Docker container, it stops that container too. Ordinary
`python manage.py runserver` still starts **only Django**.

Suggested values in the local `.env`:

```env
DJANGO_DEBUG=True
DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost
SECURE_SSL_REDIRECT=False
SESSION_COOKIE_SECURE=False
CSRF_COOKIE_SECURE=False
DB_ENGINE=django.db.backends.sqlite3
DB_NAME=db.sqlite3
DB_CONN_MAX_AGE=0
CELERY_BROKER_URL=redis://127.0.0.1:6379/0
CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/1
CELERY_TASK_ALWAYS_EAGER=False
# Enable scheduled features only when ready to test them:
ATTENDANCE_AUTO_CHECKOUT_ENABLED=False
CREDIT_BILLING_CELERY_ENABLED=False
```

If upgrading from a local setup with `SQLITE_DATABASE_PATH`, the backend
accepts that older path only for local SQLite so existing records are not lost.
Set `DB_NAME` to the same path and remove `SQLITE_DATABASE_PATH` when convenient.
Do not delete or overwrite `db.sqlite3`. On production, configure PostgreSQL
via `.env.production.example`; SQLite is not allowed with `DJANGO_DEBUG=False`.

The launcher never enables billing, attendance, or email automation flags.
It rejects `DJANGO_DEBUG=False` or local HTTPS redirects; use dedicated
worker/Beat deployments, not this launcher, in production. Run migrations
separately before first use: `python manage.py migrate`.


### Windows

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env

python manage.py makemigrations
python manage.py migrate
python manage.py check
python manage.py bootstrap_cubixgear
python manage.py runserver
```

### macOS / Linux

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

python manage.py makemigrations
python manage.py migrate
python manage.py check
python manage.py bootstrap_cubixgear
python manage.py runserver
```

Before `bootstrap_cubixgear`, change `BOOTSTRAP_ADMIN_EMAIL` and `BOOTSTRAP_ADMIN_PASSWORD` in `.env`.

## API base

```text
/api/v1/auth/
/api/v1/company/
/api/v1/branches/
/api/v1/roles/
/api/v1/dashboard
/api/v1/customers/
/api/v1/vehicles/
/api/v1/services/
/api/v1/jobs/
/api/v1/stock/
/api/v1/inventory/
/api/v1/invoices/
/api/v1/e-way-bills/
/api/v1/payments/
/api/v1/expenses/
/api/v1/employees/
/api/v1/attendance/
/api/v1/payroll/
/api/v1/reports/
/api/v1/notifications/
/api/v1/settings
/api/v1/saas/
```

## Company panel connection

In `cubegears_company-panel/.env`:

```env
VITE_API_URL=http://127.0.0.1:8000/api/v1
VITE_USE_MOCK_API=false
```

The frontend already sends `Authorization: Bearer <access-token>`.

For a local PWA test, keep Django running and run `npm run build` followed by
`npm run preview` in Company Panel. Vite Preview serves the build on port 4173.
If you already have a backend `.env`, add `http://localhost:4173` and
`http://127.0.0.1:4173` to its `CORS_ALLOWED_ORIGINS` list and restart Django.
Open `http://localhost:4173/stock` online before testing offline.

## Database — one DB_* format for local and production

Every backend deployment reads the same seven database variables:
`DB_ENGINE`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`,
`DB_PORT` and `DB_CONN_MAX_AGE`. No `POSTGRES_*` variables or
`DATABASE_URL` are read by Django; deprecated names raise a configuration
error instead of silently switching databases.

```env
DB_ENGINE=django.db.backends.postgresql
DB_NAME=cubixgear_db
DB_USER=cubixgear_user
DB_PASSWORD=
DB_HOST=localhost
DB_PORT=5432
DB_CONN_MAX_AGE=0
```

Fill the password in your **private** `.env`, not the committed example.
Create the PostgreSQL database and role first, then run
`python manage.py check` and `python manage.py migrate`. The local database
server must be running. Production uses the same format with `DB_HOST`
pointing at its database service or external provider.

- `.env.example`: local DB_* + Redis values.
- `.env.production.example` / `ENV.production.example.txt`: private production
  settings with DB_* placeholders and no real credentials.
- `compose.production.yml`: PostgreSQL, Redis, Django Gunicorn, Celery worker
  and optional one Beat. The official PostgreSQL container internally maps
  DB_* to the image-required POSTGRES_* variables.
- `scripts/check_production_config.py`: read-only security/database/Redis preflight.

Local/CI SQLite remains available **only when explicitly configured** with
`DB_ENGINE=django.db.backends.sqlite3` and `DB_NAME` pointing to a database
file; production rejects SQLite. No automatic SQLite fallback exists.
Changing database settings does **not** migrate existing workshop records.
Back up and reconcile jobs, invoices, supplier credits, customer balances,
payroll, inventory and ledger before switching a database. See
[separate-service deployment guide](docs/production-postgres-celery.md).

The React/Vite frontend stays separate (for example on Vercel) and uses
`VITE_API_URL=https://YOUR_BACKEND/api/v1`; no database credentials
belong in the frontend environment.

## Security notes

- Every business model is company-scoped.
- Normal users only query their own company records.
- Job, payment, employee and inventory relations are validated against company ownership.
- Refresh tokens can be blacklisted on logout.
- Magic-link tokens are stored as hashes and expire.
- Secrets are loaded from `.env`; do not commit the real `.env`.


## Celery email worker

CubixGear sends signup/setup-password, forgot-password and magic-link emails through Celery.

Local services:

```powershell
# Terminal 1 - Redis
redis-server
```

```powershell
# Terminal 2 - Django
venv\Scripts\activate
python manage.py runserver
```

```powershell
# Terminal 3 - Celery worker (Windows)
venv\Scripts\activate
celery -A config worker -l INFO -P solo
```

Environment:

```env
CELERY_BROKER_URL=redis://127.0.0.1:6379/0
CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/1
CELERY_TASK_ALWAYS_EAGER=False
```

For quick local testing without Redis/worker, set `CELERY_TASK_ALWAYS_EAGER=True`. In that mode Celery tasks execute inside the Django process.

Email tasks automatically retry transient failures with exponential backoff.


### Attendance automation

Attendance supports four company-wide punch modes:

- `single`: one check-in and one check-out per day.
- `multi`: multiple check-in/check-out sessions per day.
- `auto_checkout`: employee checks in; manual checkout is disabled and Celery closes the session at shift end.
- `hybrid`: one manual session; missed checkout is automatically closed at shift end.

Attendance rules also control shift times, late grace, overtime threshold, maximum sessions, missing-punch policy, weekly offs, alternate Saturdays, location-required punches and correction approval.

Run Celery worker and Beat together with Django:

```powershell
# Terminal 1
redis-server
```

```powershell
# Terminal 2
venv\Scripts\activate
python manage.py runserver
```

```powershell
# Terminal 3 - Windows worker
venv\Scripts\activate
celery -A config worker -l INFO -P solo
```

```powershell
# Terminal 4 - scheduler for auto checkout
venv\Scripts\activate
celery -A config beat -l INFO
```

The Beat schedule checks open attendance sessions every five minutes. The recorded checkout time remains the configured shift end; the worker grace interval is not counted as worked time.


## Attendance modes

Attendance rules are company-wide and managed from the Company Panel Attendance Manager.

Supported modes:

- `single`: one check-in + one manual check-out per day. No second session.
- `multi`: multiple check-in/check-out sessions per day, with optional session limit.
- `auto_checkout`: employee checks in once; manual checkout is disabled and Celery closes the session at configured shift end.
- `hybrid`: employee can manually check out once; if forgotten, Celery auto-closes at shift end.

Other controls include late grace minutes, overtime threshold, missing-punch policy, correction approval, self-approval protection, required browser location, weekly offs and alternate Saturdays.

Run the periodic auto-checkout worker with Redis:

```powershell
# Worker
celery -A config worker -l INFO -P solo

# Beat scheduler
celery -A config beat -l INFO
```

Django must also be running:

```powershell
python manage.py runserver
```

The Celery Beat schedule checks open attendance sessions every 5 minutes. Auto-closed sessions are recorded at the configured shift-end time, not at the later worker execution time.

## Root control panel

Open `http://127.0.0.1:8000/` for the SaaS control panel. Anonymous visitors
are sent to `/login/`; active staff or superusers can sign in. Platform
administrators do not need a linked company. `/panel/` is no longer mounted.
The workshop frontend API stays under `/api/v1/`, and Django admin stays at `/admin/`.

Before starting a new installation (Python 3.12+):

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

| Route | Function |
| --- | --- |
| `/` | Platform dashboard with real companies, users, wallet credits and workshop metrics |
| `/login/` | Administrator sign-in |
| `/logout/` | POST sign-out, with CSRF protection |
| `/companies/` | Companies and enable/disable actions |
| `/users/` | Accounts, passwords and enable/disable actions |
| `/branches/` | Company branches |
| `/roles/` | Roles and permission checkboxes |
| `/plans/` | Existing plan catalog and plan configuration (legacy payment records are retained) |
| `/credits/` | Credit packages, company wallets and transaction ledger |
| `/settings/` | Storage and credit-cycle billing policies |
| `/customers/` | Customers |
| `/vehicles/` | Customer vehicles |
| `/jobs/` | Job cards |
| `/stock/` | Stock items |
| `/invoices/` | Billing documents |
| `/staff/` | Workshop employees |
| `/storage/` | Storage usage records |
| `/security/` | Security event records |

Management lists provide applicable create/edit/detail/delete actions depending on the module. Storage/security and credit/billing settings have their own dedicated controls. Legacy Control Panel subscription CRUD/payment-history routes were retired; existing SaaS subscription and Razorpay models/APIs remain for historic access and payment reconciliation.
Deletes require a confirmation POST. Lists paginate at 50 records and preserve
search/filter parameters. Existing records cannot be reassigned between companies;
related records are checked against the selected company. Administrators cannot
remove their own access flags, disable themselves or delete themselves.

Run panel regression checks with `python manage.py test apps.control_panel`.


## Role access and direct email delivery

- Active Django superusers have full platform and API access. `is_staff` alone
  never grants access to the custom control panel or workshop APIs.
- Workshop accounts require an active role from the same company (or an assigned
  shared role) and an active company. Permissions use codes such as `jobs.view`,
  `jobs.edit`, `stock.edit`, `payroll.view`, and `company.manage`. Unassigned
  permissions are denied server-side. Stock issue/return/inward compatibility
  actions require both `jobs.edit` and `stock.edit`.
- Workshop roles, including a tenant `*` owner, cannot access the global panel.
  To delegate platform administration, a superuser creates a companyless role
  with explicit `panel.<resource>.<view|create|edit|delete>` permissions and assigns
  it to an active companyless staff account. Dashboard/storage/security are view
  only. Navigation and action links follow the same permission checks.
- Managers cannot grant or assign permissions beyond their own authority, modify
  more privileged roles/accounts, attach another company's login account, or set
  `is_system` through the roles API. A superuser should manage privileged accounts.
- `/api/v1/auth/me` and login responses include `effectivePermissions` for frontend
  navigation/actions. Refresh this data after role changes; backend checks apply
  immediately on every new request. The separate company-panel frontend must use
  this field to hide its own controls.
- Roles in the root panel use permission checkboxes. `*` is full administration
  within the account's scope, so assign it only to owners/administrators.

Account reset, signup, magic-link and staff invitation emails now call Django's
email backend synchronously after the database transaction commits. No Redis or
Celery worker is needed for email. `apps/accounts/tasks.py` retains the retrying
Celery task; the equivalent queue call is commented in `apps/accounts/email.py`
for a later switch. Scheduled attendance tasks still need their existing worker.
SMTP failures are logged by the commit callback; public account-recovery endpoints
retain their generic response. Direct delivery has no automatic retry/outbox, so
monitor SMTP errors and retry a failed invitation/reset request as appropriate.

For production configure `DJANGO_DEBUG=False`, strong unique secret/JWT keys,
explicit allowed hosts, PostgreSQL, an HTTPS reverse proxy, and
`CSRF_TRUSTED_ORIGINS=https://<backend-host>`. HTTPS redirect and secure cookies
are enabled when DEBUG is false; HSTS defaults to one hour and is configurable.
Set `EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend`, SMTP host, port,
username, password, TLS and a verified sender. `EMAIL_TIMEOUT` defaults to 15s.
The console backend is for local development and does not deliver email.
Run `python manage.py check --deploy`, migrations and a real SMTP smoke test in
staging before release. No deployment or live SMTP credentials are bundled.


## Customer Website (separate Next.js frontend)

Frontend: https://github.com/amnas-cubixmet/cubegears-customers-website

One Next.js deployment serves all workshop websites at
`https://<label>.customer.cubixgear.tech`. **Do not create another Django
Company model or another Vercel project on every signup.** Signup already
creates a `CompanyWebsiteSubdomain` reservation, and migration
`apps.companies.0009_company_website_subdomain` backfills existing workshops.
Changing the public website label never changes the staff login `Company.slug`.

The Next.js same-origin route `/api/portal/*` signs each request using
`PORTAL_PROXY_SHARED_SECRET` and forwards it to Django at
`/api/v1/customer-website/*`. Django verifies the signature and resolves
only an active Company with a matching registered hostname. Browser-supplied
company IDs are not accepted for tenant selection. The public homepage
only returns public branding; private data requires a valid customer link
exchanged for a tenant-scoped HttpOnly session cookie.

### Configuration

Set **in Django's private runtime environment**:

```env
# Actual HTTPS host on which this Django service runs.
DJANGO_ALLOWED_HOSTS=api.cubixgear.tech
CUSTOMER_PORTAL_ROOT_DOMAIN=customer.cubixgear.tech
PORTAL_PROXY_SHARED_SECRET=<same-random-value-at-least-32-chars-as-nextjs>
# Temporary link fallback must be a real, existing endpoint if links are disabled.
CUSTOMER_PORTAL_BASE_URL=https://customer.example.com/access
CUSTOMER_WEBSITE_LINKS_ENABLED=False
```

Set **only on the Next.js server/Vercel project** (not `NEXT_PUBLIC_*`):

```env
CUBEGEARS_BACKEND_URL=https://api.cubixgear.tech
CUSTOMER_PORTAL_ROOT_DOMAIN=customer.cubixgear.tech
PORTAL_PROXY_SHARED_SECRET=<same-random-value-at-least-32-chars-as-django>
```

Use an actual private secret generated locally; never commit populated env files.
`CUBEGEARS_BACKEND_URL` must be the **origin**, not `/api/v1`.
The Next.js `PORTAL_BACKEND_URL=http://127.0.0.1:8000` option is for local
development only. The production customer wildcard hostname does **not** need
to be added to Django `ALLOWED_HOSTS`: Next.js forwards the request to
the Django API domain using its signed tenant-host headers.

`DJANGO_DEBUG=False`, persistent PostgreSQL, HTTPS, valid backend allowed
hosts, a secure Django secret/JWT signing key, secure session cookies and
migrations are required before deployment. Keep the backend reverse proxy's
`X-Forwarded-Proto` correct. Never set the same browser-wide session-cookie
domain for every customer subdomain.

### Wildcard DNS and production rollout

For Vercel, attach `*.customer.cubixgear.tech` to the **single** Customer
Website project. Vercel's official wildcard setup requires the **nameservers
method** to support certificate issuance/renewal. Do not assume that adding
a wildcard CNAME and delegating only `_acme-challenge` is sufficient.
If the existing apex `cubixgear.tech` DNS is managed elsewhere, plan a
careful `customer.cubixgear.tech` DNS-zone delegation with your provider
and Vercel, or use the existing reverse proxy with a managed wildcard
certificate. Preserve live API, app, mail and unrelated DNS records.

1. Deploy Django and Next.js using their respective production environment
   variables; run `python manage.py migrate --noinput`.
2. Configure and verify wildcard DNS/TLS, and create or identify an **active**
   workshop label in the Django production database.
3. Run `npm run verify:portal -- <workshop>.customer.cubixgear.tech` from
   the frontend repo. It checks branding HTTP 200, unauthenticated session
   HTTP 401, and an unknown workshop HTTP 404 (without access tokens).
4. Generate a **test customer's** access link from the Company Panel, redeem
   it on the correct workshop hostname, and verify authorized vehicle and
   invoice data. Test that another workshop hostname cannot read the session.
   Do not paste or log actual access links, cookies or private payloads.
5. Only after successful checks, change
   `CUSTOMER_WEBSITE_LINKS_ENABLED=True` in Django and restart the service.
   The setting reports deployment readiness by operator choice; it does not
   query live DNS/SSL automatically.

### Existing endpoints

- `GET /api/v1/company/customer-website` — workshop branding and label
- `PATCH /api/v1/company/customer-website` — workshop branding
- `PATCH /api/v1/company/customer-website/subdomain` — change public label
- `GET /api/v1/customer-website/branding` — public workshop branding
- `POST /api/v1/customer-website/session` — exchange private access capability
- `GET /api/v1/customer-website/session` — authorized customer dashboard
- `POST /api/v1/customer-website/logout` — close private session
- `GET /api/v1/customer-website/logo`, `photos/<uuid>`, `invoices/<uuid>/pdf`
  — subject to their existing per-company/authorization checks

The former per-workshop custom-domain registration endpoints were **removed**
by migration `apps.companies.0010_remove_custom_domains`. Do not configure
legacy `/customer-website/domains` or Vercel per-workshop API tokens.

Tests:
`python manage.py test apps.companies.tests_default_subdomains apps.customers.tests_website`.
