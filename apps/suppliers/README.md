# Suppliers Django app

This is the **canonical supplier-domain API** for CubeGears.

## Data compatibility

Existing `inventory.Supplier`, `SupplierBill`, `SupplierPayment`,
`SupplierPaymentAllocation`, `WorkshopFinancialAccount`, and
`WorkshopAccountEntry` remain at their existing inventory model labels and
physical database tables. `apps.suppliers.models` re-exports those models.
No destructive model move, table recreation, or data migration is performed.
A later physical ownership migration requires a separately reviewed migration
plan for all foreign keys, content types, permissions and third-party clients.

## API base

New: `/api/v1/suppliers` (also accepts `/api/v1/suppliers/`).

- `GET /api/v1/suppliers` — list and search suppliers
- `POST /api/v1/suppliers` — create supplier
- `GET /api/v1/suppliers/{id}` — details
- `PATCH /api/v1/suppliers/{id}` — edit/deactivate
- `DELETE /api/v1/suppliers/{id}` — only when unreferenced
- `GET /api/v1/suppliers/{id}/summary` — financial summary
- `GET /api/v1/suppliers/{id}/ledger` — financial ledger
- `GET/POST /api/v1/suppliers/procurement/accounts`
- `GET /api/v1/suppliers/procurement/accounts/{id}/ledger`
- `GET/POST /api/v1/suppliers/procurement/bills`
- `POST /api/v1/suppliers/procurement/bills/{id}/post`
- `GET/POST /api/v1/suppliers/procurement/payments`
- `POST /api/v1/suppliers/procurement/payments/{id}/allocate`

The existing `/api/v1/stock/suppliers`, `/api/v1/inventory/procurement/*`
and `/api/v1/stock/procurement/*` routes are **preserved** and resolve to
these same canonical view classes. Do not create parallel balances or ledgers.

## Permissions

For compatibility, Supplier CRUD uses the existing `stock.*` permission and
supplier account/payment endpoints keep their existing `stock.*` and
`payments.*` guards. This migration does not create a separate paid feature
or change owner/tenant authorization. Granular `suppliers.*` permissions require
an explicit coordinated migration of the role catalog, feature entitlements,
UI and backend guards.

## Developer validation

Run `python manage.py check`, `python manage.py makemigrations --check --dry-run`,
`python manage.py test apps.suppliers apps.inventory.tests_supplier_finance apps.inventory.tests_supplier_credit`.
