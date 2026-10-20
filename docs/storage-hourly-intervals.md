# Storage meter hours and interval logs

## Billing settings

At `/settings/`, Storage Pricing displays **Meter interval (hours)**.
The form converts hours to the existing `StorageCreditPolicy.meter_interval_seconds`
column on save; the billing engine, legacy clients and scheduled tasks continue
to use seconds. Example: `1` hour stores `3600` seconds; `2.5` stores `9000`.
This **billing meter** affects legacy metered monthly usage only, not the prepaid
snapshot method or an independently configured calendar billing engine.

**Interval Storage Logs** is a separate monitoring policy on the same page.
Enable it and choose 1, 2, 6, 12, or 24 hours to record actual usage checkpoints.
Prepaid snapshots can use this read-only monitoring without changing billing.
Monitoring is disabled until an admin enables it.

## Persistence

Migrate with `python manage.py migrate`. Migration 0022 adds
`StorageMonitoringReading`, uniquely keyed by company and sampled timestamp.
Each due monitoring tick persists one record per active company. The existing
date-granular `StorageUsage` row remains a latest-daily view and is not used
as an hourly audit ledger. Missed ticks are not fabricated. The monitor never
changes wallet balances or billing period settlements.

Celery requires Redis, one worker, and **exactly one** Beat scheduler.
The task `cubegear.storage.monitoring.snapshot_due` checks the configured
monitoring policy each minute and writes only when due.

## Read-only interval API

`GET /api/v1/saas/storage/interval-logs?from=YYYY-MM-DD&to=YYYY-MM-DD&limit=50&offset=0`

Responses have `count` and a newest-first `results` list, with source:
`monitoring` (unbilled), `monthly_meter`, or `legacy_meter`.
Each row includes `sampledAt`, `usedBytes`, `usedGb`, `fileCount`
(if recorded), `intervalSeconds` or `coveredSeconds`, and
`accruedCredits` (only if persisted by an actual billing meter).

The API uses the existing storage account read permission, filters every table
to `request.user.company`, validates date ranges, caps page size at 100,
and caps offsets at 5000. The company admin detail displays recent records
in a table; the company panel at `/account/storage` lists paginated readings.

## Verification

```powershell
python manage.py migrate
python manage.py check
python manage.py test apps.credits.tests_storage_interval_monitoring
```

Frontend:

```powershell
npm test
npm run build
```

Do not enable monitoring or any billing policy on production until the scheduler
and expected observation cadence are validated in staging.
