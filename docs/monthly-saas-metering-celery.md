# CubeGear: monthly metered SaaS features + storage

**STATUS: opt-in migration. All metered debit flags are OFF by default.**
Run tests and reconcile old prepaid entitlements/bills before activation.
This does not change Razorpay: rupees buy wallet credits; monthly usage
debits wallet credits. There is no assumed 1 rupee = 1 credit conversion.

## How it works

- Company features are *company-wide* and every employee still requires
  backend role permission; purchases never grant RBAC.
- The backend `GET /api/v1/saas/credits/access` returns actual effective
  permissions after both RBAC and paid entitlement checks.
- Staff frontend sidebar, mobile navigation, route guards and dashboard
  widgets use this endpoint. Disabled paid modules are filtered. The
  backend also redacts dashboard data by permission (not merely client CSS).
- Staff with no enabled modules see the feature-activation landing instead
  of workshop statistics. Only users with `billing.edit` can buy/enable
  modules. Non-admin staff are directed to their company administrator.
- The *legacy prepaid* feature charging is still supported. Postpaid is
  configured separately; the same company/feature cannot be active in
  prepaid and monthly-postpaid billing simultaneously. Paid bundles also
  reject overlapping active postpaid module purchases.
- Postpaid feature rate is **credits per calendar month**:
  `base_credits + per_user_credits × active login users + per_staff_credits × active employees`.
  Activation fee is accrued once per company/feature/month. Every cycle,
  measured seconds contribute a prorated charge to that month's total;
  users/staff/rate are re-sampled. Turning OFF a feature stops future usage.
- Postpaid storage rate is **credits per measured GB/month**. Free GB per
  company is deducted at each reading. Credits accumulate proportionally
  to observed time within each calendar month. Upload/delete changes are
  reflected at the next server-side observation, not browser reports.
- Both engines store immutable readings and month-level accruals. On/after
  the first day of the next month, closed periods are debited once with
  `CreditLedger.reference`:
  `feature-metered:<company>:<feature>:YYYY-MM` or
  `storage-metered:<company>:YYYY-MM`.
- If the wallet lacks credits, **no negative balance is allowed**. The
  period remains unpaid for a future retry, and postpaid feature access is
  suspended. Existing paid period ledger rows remain immutable.
- Worker downtime cannot be reliably reconstructed from current files or
  current staff: intervals longer than 2 × the configured polling interval
  are skipped entirely and logged. This is a conservative sampled-usage
  estimate, **not exact second-by-second event billing**.

## Flags: keep OFF until ready

In backend `.env`, never in the frontend and never commit secrets:

```dotenv
CREDIT_FEATURE_ENFORCEMENT_ENABLED=false
CREDIT_FEATURE_STRICT_GATE_ENABLED=false
CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED=false
STORAGE_MONTHLY_METERED_ENABLED=false
STORAGE_METER_INTERVAL_SECONDS=300
CELERY_BROKER_URL=redis://127.0.0.1:6379/0
CELERY_RUN_FEATURE_BILLING=false
CELERY_RUN_STORAGE_BILLING=false
```

For rollout, reconcile all registered permission modules first using
`python manage.py audit_feature_catalog`; then audit active prepaid
access/bundles and each company's permissions; then change feature
enforcement/strict/postpaid flags together after testing. Do not turn on
`CREDIT_FEATURE_STRICT_GATE_ENABLED` before all modules are registered,
or users can lose access to legitimate features. An existing active
prepaid module must be switched OFF before being started as postpaid.

### Local safe checks

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py migrate
python manage.py test apps.credits.test_monthly_metered_billing apps.credits.test_metered_feature_pricing apps.credits.test_feature_bundles --noinput
python manage.py meter_monthly_storage
python manage.py settle_metered_storage
python manage.py meter_monthly_features
python manage.py settle_metered_features
```

Preview commands **never deduct credits**.
To record readings after enabling config flags, run
`python manage.py meter_monthly_storage --execute` and
`python manage.py meter_monthly_features --execute`.
To settle **closed** months, explicitly run
`python manage.py settle_metered_storage --execute` and
`python manage.py settle_metered_features --execute`.
These charge live wallets; run only after a verified dry-run.

**NEVER** run the legacy `bill_storage_credits` command when the
new monthly meter is enabled. Both its CLI and the direct legacy service
block attempted double-charging. Legacy prepaid feature renewals are
separate and retain their existing scheduled behavior.

## Celery + Redis (optional dedicated worker)

CubeGear's web-only PythonAnywhere WSGI environment does not run
Celery automatically. For a Windows local test, start Redis in Docker
(if available):

```powershell
docker run -d --name cubegear-redis -p 6379:6379 redis:7-alpine
pip install "celery[redis]>=5.6,<5.8"
celery -A config.celery:app worker --loglevel=info --pool=solo
```

In a **different** terminal, run exactly one scheduler:

```powershell
celery -A config.celery:app beat --loglevel=info
```

Only with a valid `CELERY_BROKER_URL`, the relevant
`STORAGE_MONTHLY_METERED_ENABLED` / `CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED`
and `CELERY_RUN_*_BILLING=true` are jobs installed.
When enabled, sampling runs once per configured number of seconds,
closed storage months settle around **00:20 Asia/Kolkata** and feature
months around **00:25 Asia/Kolkata** each day (unpaid months retry).
One worker/Beat deployment is recommended; DB locks/unique ledger references
also prevent double debits if a job is retried.

The existing `apps.accounts.tasks.send_email_task` can be processed by
this same worker once configured. Staff invitation email currently uses
Django post-commit direct delivery and is **not automatically converted**
to Celery by these changes. Configure SMTP separately and test delivery.
Do not send or log email credentials, reset tokens or invite URLs.

## Operational cautions

- New metering starts at the first actual reading; it does not invent
  historical usage and cannot retroactively bill before setup.
- `STORAGE_METER_INTERVAL_SECONDS` accepts 60–86,400; 300 seconds (5 min)
  is a conservative default. Do not schedule faster than 60 seconds.
- Monthly storage policy must be active and set to `monthly`.
- No automatic bank withdrawal occurs: this is a **prepaid wallet deduction**
  at month close. Failed charges stay outstanding.
- Monitor Celery/Redis uptime, unpaid settlements and ledger discrepancies.
  Introduce billing notices, contractual grace periods, provider audit and
  robust metering-event reconciliation before declaring production-ready.
