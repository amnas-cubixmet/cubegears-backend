# CubeGear — guarded Celery monthly credit billing

## Operational contract

This is an **additive, opt-in** monthly postpaid billing mode. All existing
prepaid feature/bundle purchases, stored company wallet balances, invoices,
media and role permissions remain unchanged until the flags below are enabled.

The frontend fetches `GET /api/v1/saas/credits/entitlements` to display **only
backend-authorized modules**. Role permission remains independently enforced
for every protected API operation. If the entitlement API is missing/unavailable,
the frontend fails closed. The feature marketplace and wallet top-up remain
available when the company has no activated features.

## Monthly billing versus historical 30-day prepaid charges

* **Monthly-postpaid individual features:** only `FeatureCreditPrice` rows
  configured for `monthly` and activated *after* enabling postpaid mode are
  metered. Enabling stores rate snapshot and opens an entitlement without
  debiting credits. Every metering cycle records exact elapsed feature seconds
  with currently observed active **login-user** and employee counts. Turning
  OFF closes the session immediately; historical accrual remains due. Activation
  fees are recorded once per actual enable.
* **Storage:** trusted upload/delete StorageEvent history allows calculating
  month-weighted `billable GiB × elapsed seconds / calendar-month seconds`;
  the first Celery sample creates baseline events for existing files. Usage
  before that first baseline is **not inferred or charged**. Existing
  per-GB credit rate/free GB policy is applied to the finalized month; pricing
  updates currently apply when the month statement is created, not historic
  price-version windows.
* **Settlement:** one immutable `MonthlyCreditStatement` per company/month,
  computed no sooner than the first day of the next calendar month.
  `monthly_usage` wallet ledger debit uses one unique
  `postpaid-month:<company>:YYYY-MM` reference.
  If balance is insufficient, the statement remains **pending**; subsequent
  Celery retries can settle it after a verified top-up, without a second debit.
  Outstanding invoices restrict paid-feature access but do not block buying
  credits or viewing billing history.
* Existing prepaid 30-day individual features and paid label bundles remain
  prepaid until individually expired/disabled. Do **not** retroactively
  convert those purchases without a data migration and tenant notice.
* When postpaid mode is active, the old monthly snapshot
  `bill_storage_credits` path is blocked to prevent duplicate storage charges.
  Daily storage billing retains its existing separate behavior. Use
  **monthly**, not daily, storage policies for unified statements.

## Setup (Windows development)

Ensure Redis is running and the latest repository dependencies are installed:

```powershell
pip install -r requirements.txt
python manage.py migrate
python manage.py check
python manage.py test apps.credits.test_postpaid_monthly_billing --noinput

# Backend web app, terminal 1:
python manage.py runserver

# Worker, terminal 2:
celery -A config worker --loglevel=info --pool=solo

# Beat, terminal 3: **exactly one** process per deployment:
celery -A config beat --loglevel=info
```

First configure and review a TEST tenant and valid rates. Set:

```dotenv
CELERY_BROKER_URL=redis://127.0.0.1:6379/0
CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/1
CREDIT_METER_SECONDS=60
CREDIT_FEATURE_ENFORCEMENT_ENABLED=true
CREDIT_POSTPAID_BILLING_ENABLED=true
CREDIT_AUTOMATIC_BILLING_ENABLED=true
```

Restart all three processes after changing settings. Production metering
frequency is clamped to 60 seconds minimum; development may use 10 seconds
for controlled test cycles. Ledger accumulation is calendar-month prorated,
not a full credit charge on every cycle.

**Important:** A live deployment must run **one** Beat instance. Run tests
on disposable data before switching flags on. Ensure durable Redis, reliable
worker restart, correctly configured SMTP for Django authentication emails,
and operations alerts for failed tasks. Authentication emails continue to use
Django transaction.on_commit direct SMTP; this change does not silently reroute
password/token emails through a queue or log secrets.

Monthly statement API: `GET /api/v1/saas/credits/monthly-statements`.
Feature controls: `/api/v1/saas/credits/features`.
Backend wallet ledger: `/api/v1/saas/credits/logs`.

## Limitations before a production-wide rollout

- Active login-seat count changes are observed at each metering cycle, not
  captured by account-change event hooks. At 60-second cadence, adjustments
  may be delayed up to one sample.
- Unmetered time before enabling the first baseline is not charged.
- Rate changes are snapshotted for activated features, but the current
  storage policy is used when generating the statement. If rates change
  during a month, the old/new storage-rate boundary is not represented.
- Celery does not retroactively recover storage upload/delete events that
  bypass the tracked MediaFile/StorageEvent API.
- No invoice email alerts or collection webhook is added here. Completed
  wallet debit is in **credits**, not an INR cash payment.
- Monthly package bundles still use their existing **prepaid 30-day** logic;
  they are not migrated to the new postpaid individual-feature model.
- Rollout requires review of prior subscriptions/entitlements and
  end-to-end staging with actual user toggles and ledger reconciliation.

No worker is launched, no real credits charged, and no environment flags
changed by merging this code alone.
