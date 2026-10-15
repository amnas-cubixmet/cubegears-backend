# CubeGear optional Celery billing deployment

**This is a safe scheduler for the existing PREPAID feature renewal and storage
period-snapshot billing paths, not a new postpaid usage-cycle billing engine.**
No new debit path activates unless `CREDIT_BILLING_CELERY_ENABLED=True`.
Plan and test a separate accrual ledger before promising GB-seconds or exact
calendar-month feature usage settlement.

## Install

```powershell
pip install -r requirements-celery.txt
```

Provision a persistent Redis server and set these variables in the backend
environment (never commit actual passwords):

```dotenv
CREDIT_BILLING_CELERY_ENABLED=False
CELERY_BROKER_URL=redis://127.0.0.1:6379/0
CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/1
```

First verify the existing billing policies/prices and service commands on
a local test database:

```powershell
python manage.py charge_credit_features --dry-run
python manage.py bill_storage_credits --dry-run
```

When explicitly approved, set `CREDIT_BILLING_CELERY_ENABLED=True` and
restart backend and worker. In **separate terminals**:

```powershell
celery -A config.celery:app worker --pool=solo --loglevel=info
celery -A config.celery:app beat --loglevel=info
```

On Linux, omit `--pool=solo` and choose supported worker concurrency.
Beat schedules one daily renewal at 02:05 and one storage billing task
at 02:15 in `DJANGO_TIME_ZONE` (defaults Asia/Kolkata).

**Run exactly one Beat instance and do not simultaneously schedule
`charge_credit_features` or `bill_storage_credits` using cron/Windows
Task Scheduler.** The ledger references prevent duplicate wallet debits;
a competing scheduler is still an operations incident.

Never turn on these tasks while migrating existing feature billing to
postpaid monthly cycles. The old feature operation debits on activation
and renews at the feature-specific due date, not at calendar month-end.
Storage billing is a current-byte snapshot, not GB-hour averaging.

Email delivery is a separate Django SMTP setting (`EMAIL_HOST`,
`EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`) and is not automatically
processed by this worker. Missing broker, failed retries and insufficient
wallet balance must be monitored operationally.
