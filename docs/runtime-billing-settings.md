# Admin billing timing and change tracking

Open `/credits/calendar/` from the backend control panel (or the calendar billing
form on `/storage/`). Only platform administrators with credit-policy edit
permission can change this policy.

- Usage closes on the last day of each calendar month; settlement remains one
  idempotent combined debit per company/month.
- **Due day + due time** select when withdrawal can start in `DJANGO_TIME_ZONE`.
  Existing finalized statements keep their original deadline. Overdue restriction
  still starts the following day, rather than at the withdrawal time.
- **Rate duration seconds** is 0 for a calendar-month rate, or an explicit number
  of seconds (60–31622400). It changes rate normalization, not the calendar
  statement boundaries. For example, 86400 makes a configured 1-credit/GB rate
  accrue 1 credit for one GB kept for one day.
- **Calculation interval** controls feature accrual; **storage tracking interval**
  controls storage accrual (each 60–86400 seconds). The minute heartbeat reads
  these database settings on each run, so timing edits do not require restarting
  Beat. The heartbeat is accurate to its next minute tick plus worker queue delay.
- **Event tracking** is explicitly opt-in. New installations and existing
  installations keep the previous behavior until the administrator enables it.

## Start locally (Windows PowerShell)

Install into the backend virtual environment, then run:

```powershell
pip install -r requirements-celery.txt
python manage.py migrate
```

Backend `.env`:

```env
CELERY_BROKER_URL=redis://127.0.0.1:6379/0
CREDIT_BILLING_CELERY_ENABLED=True
STORAGE_MONTHLY_METERED_ENABLED=True
CREDIT_FEATURE_ENFORCEMENT_ENABLED=True
CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED=True
CREDIT_LABEL_ONLY_ACCESS_ENABLED=False
CREDIT_POSTPAID_FEATURE_BILLING_ENABLED=False
```

Use a running Redis server. In separate activated backend terminals:

```powershell
python manage.py runserver
```

```powershell
celery -A config worker --loglevel=info --pool=solo
```

```powershell
celery -A config beat --loglevel=info
```

Start exactly one Beat process. Email delivery can use this same worker and
broker; its `EMAIL_CELERY_ENABLED` and SMTP configuration remain separate.
After changing environment flags restart Django, worker and Beat. After editing
runtime timing in the admin form, no restart is required.

Configure one monthly storage price (`snapshot` is the existing model's name for
the new time-weighted engine), enable calendar policy and event tracking, then
activate monthly postpaid features through the existing feature controls. Do
not enable a legacy CreditCyclePolicy or MonthlyBillingPolicy alongside it.

```powershell
python manage.py check_celery
python manage.py settle_calendar_credits --dry-run
```

## Storage and staff changes

The first event/measurement counts existing MediaFile metadata once. Subsequent
application saves update a transactional byte counter. Uploads, replacements,
size edits and deletes checkpoint old bytes up to the mutation timestamp, then
install new bytes. Rollbacks undo counters, history and accrual together. A
same-name replacement is recognized by the bytes/file field, not by its name.
Unchanged files accrue again next month. Deletion stops future storage cost;
already accrued cost is retained.

A tenant-scoped multipart `PATCH /api/v1/saas/storage/files/<file-id>` accepts
`file` for replacements, validates the same MIME/size/quota rules as upload,
retains the display name and deletes the old blob only after database commit.

Active Employee status changes and linked login account activation changes
checkpoint feature rates immediately. In event mode per-staff pricing counts
active employees with an active workshop login (or no linked login), while
per-user pricing counts active workshop logins linked to active employees.
Platform staff/superusers and ordinary customer records do not enter the login
count. Logging out does not deactivate a paid staff seat. A configured paid
feature can remain ON with zero active seats and accrue zero seat charges.
Feature OFF closes its current interval; it does not erase accrued usage.
Base rates and one-time activation charges, if configured, still apply.

Bulk ORM writes (`update`, `bulk_create`, `bulk_update`) and direct filesystem or
object-store writes bypass model saves. Route normal changes through model.save
or these APIs. There is no claim that external same-name overwrites can be
noticed without an event or a scan. For imports or external changes, run the
explicit audit first, then repair from the observation time:

```powershell
python manage.py reconcile_storage_usage --company COMPANY_UUID --check-files
python manage.py reconcile_storage_usage --company COMPANY_UUID --check-files --apply
```

`--check-files` reads actual object sizes; normal Celery ticks never do this.
The repair does not invent a historical timestamp for an unobserved change.

Timing/rate-duration edits through the policy form checkpoint the old snapshots
before saving the new policy. Enabling/re-enabling is forward-only; existing
historical months and disabled periods are not newly back-billed.

## Isolated worker + Beat verification

With `redis-server` installed, this script creates private Redis, worker, Beat,
actual file data and a temporary migrated database, then removes all of them:

```powershell
python scripts/smoke_runtime_billing.py
```

Use `--redis-server PATH` when the executable is not on PATH. No existing
broker, tenant data, SMTP server or wallet is modified.
