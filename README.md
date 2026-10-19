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

## Database — SQLite only

CubixGear now uses a SQLite database file by default, at `db.sqlite3` in the backend folder. No Prisma or PostgreSQL connection variables are needed.

```env
# optional; leave unset for ./db.sqlite3 during local development
SQLITE_DATABASE_PATH=/var/lib/cubixgear/db.sqlite3
```

Use `python manage.py migrate` to create/update tables. Do not add `db.sqlite3` to Git; it is ignored.

For a live business backend, use a **single Django application instance** on a server that has durable writable storage (for example, a VPS with a persistent disk). Mount the database path outside the deployment source tree and schedule safe SQLite backups, including restore tests. SQLite permits many readers but only one writer at a time; it is not a replacement for a distributed database with many concurrent workers.

**Vercel Functions cannot persist a live SQLite database.** Keep the React/Vite frontend on Vercel, but host Django on a VPS or another service with a persistent volume. Deployment with `VERCEL=1` and `DJANGO_DEBUG=False` intentionally fails rather than risking data loss.

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
