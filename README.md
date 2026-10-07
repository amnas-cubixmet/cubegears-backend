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

## Database

Local development defaults to SQLite. Production can use PostgreSQL by setting:

```env
DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/DBNAME
```

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
