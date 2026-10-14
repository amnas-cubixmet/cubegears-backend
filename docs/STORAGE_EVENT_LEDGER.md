# CubeGear Storage Ledger — implementation guide

Inspired by CubixSkill storage event accounting. Adapted for CubeGear's existing Django/SQLite and React code.

## Included

- Database-backed company-scoped StorageEvent ledger with immutable-by-API UPLOAD/DELETE/BASELINE records.
- Records file UUID, name, category, MIME, size, actor, timestamp, signed delta, prior usage, usage-after and active file count.
- Existing files get a baseline on the first new storage action; this is a rollout-time baseline, not their original upload time.
- Upload/delete logging is written transactionally with database file records.
- Storage uploads enforce configured file size/type and active plan quota, when an active plan definition exists.
- File downloads use a JWT-authenticated, company-scoped API endpoint.
- History endpoints reconstruct day-end inventory and upload/delete events. No fake prices or charges.
- Tenant separation checks and migration verification in GitHub Actions.

## Endpoints

- `GET /api/v1/saas/storage` — current usage/files/quota.
- `POST /api/v1/saas/storage/files` — multipart photos.
- `POST /api/v1/saas/storage/files/delete` — JSON `{"ids": ["uuid"]}`.
- `GET /api/v1/saas/storage/files/<uuid>/download` — authorized download; attach Bearer JWT.
- `GET /api/v1/saas/storage/events?from=YYYY-MM-DD&to=YYYY-MM-DD&eventType=UPLOAD&limit=50&offset=0` — paginated tenant-scoped event log.
- `GET /api/v1/saas/storage/history` — event-day snapshots, falling back to legacy snapshots for accounts without events.
- `GET /api/v1/saas/storage/history/YYYY-MM-DD` — files/events at end of event day.

## Before production

1. Backup SQLite and uploaded media.
2. Run `python manage.py makemigrations --check --dry-run` and `python manage.py migrate`.
3. Run `python manage.py test apps.saas.test_storage_events apps.saas.tests_account --noinput`.
4. Configure persistent, private media storage; **never** map `/media/` to an unprotected public Nginx path.
5. Verify first upload / delete baseline on the existing database, quota rejection, tenant-scoped events, authorized file download and authenticated browser behavior.
6. Review storage migration, test all relevant file models and make sure frontend only requests /downloads files with Bearer authorization.

## Boundaries

This ledger is for `saas.MediaFile` objects. Private job photos and other Django FileField records are outside the scope.
It is not storage-credit billing, immutable database infrastructure, or legally compliant audit retention. Direct database or filesystem changes bypass the ledger. For strict compliance use off-server tamper-evident copies and retention policy.

File system deletes run on transaction commit and may fail separately from the database; operational logging reports these failures. On SQLite the company row lock does not provide PostgreSQL-grade concurrency isolation. Single-instance usage is recommended pending load testing.
