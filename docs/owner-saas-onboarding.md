# Owner-only SaaS onboarding

Public `POST /api/v1/auth/signup` creates a company on credits, records its signup user as `Company.owner`, and enables `require_paid_features`. It creates no paid feature access and no free attendance entitlement. The owner reaches `/account` after password setup or login. With no active paid modules, workshop navigation and Dashboard stay locked.

Ownership is separate from roles. A second user with `SUPER_ADMIN`, `*`, or `billing.edit` does not receive SaaS billing controls. Credits, purchases, feature management, statements and legacy SaaS payment histories require the active company owner. Staff can still fetch the read-only effective access endpoint for navigation. Every workshop API continues to require both its role permission and company entitlement.

## Apply together

Deploy this backend and the matching company-panel branch together. Run:

```bash
python manage.py migrate
python manage.py audit_feature_catalog --sync
python manage.py check
```

The catalogue audit registers actual permission modules without inventing prices. Its new entries start inactive. In the platform panel, publish the required modules and create labels at `/features/bundles/add/`. A label can contain one module or several; set the per-user credits and hourly, daily, weekly, monthly or permanent period. The server calculates `published per-user rate × active company login accounts`. `/credits/` also retains standalone module pricing and storage policy controls. Per-use labels remain unavailable until a real server operation consumes prepaid uses.

## Existing companies

The migration does not lock existing workshop modules. It identifies an existing owner only when the company's recorded email matches exactly one user with that company's system `SUPER_ADMIN` role. It never guesses another staff member. For other companies, choose the correct active owner in `/companies/<id>/edit/` before enabling billing access. The platform company editor scopes owner choices to that tenant. Enable **Require paid features** there when ready to apply the new module policy to a legacy company.

Company-facing APIs cannot change ownership, the module enforcement policy or the tenant identifier. Disabling an owner or their company removes SaaS access. Ownership can be repaired by a platform administrator through the company editor.

## Switching modules and retaining records

Switch Off revokes access without deleting customer, job, employee, salary or attendance records. An unexpired or permanent label can be switched back on without a second charge. Extending an already active time-limited label purchases another period. There is no refund for switching a prepaid label off. Company-scoped activation/disable/resume events record the actor and time and are visible under Account → Logs, alongside wallet debits and monthly statements.

## Metering and background tasks

This change reuses the existing billing engines and does not turn on workers or change deployed payment settings. Labels use their selected prepaid duration. Existing monthly postpaid feature pricing continues to use timed usage and active user/staff rate snapshots. Its metering/settlement switches are `CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED` and `CREDIT_FEATURE_ENFORCEMENT_ENABLED`; storage uses `STORAGE_MONTHLY_METERED_ENABLED`. `STORAGE_METER_INTERVAL_SECONDS` is the existing observed-gap tolerance for the newer monthly engines. The existing Celery schedules sample features every five minutes and storage every minute. The alternative legacy storage engine uses `StorageCreditPolicy.meter_interval_seconds` instead. Use only one engine per category and one scheduler instance; do not combine equivalent cron and Celery jobs. See `config/settings.py` and the existing billing runbooks before changing live billing.

Account statements read persisted settlement invoices or metered periods according to the selected engine. Reading an account or statement never deducts credits. Active-month amounts are accrued usage; settlement remains restricted to closed months.

## Tenant subdomain

The unique company slug is the reserved subdomain identifier shown on SaaS Account. Optionally configure `TENANT_BASE_DOMAIN=your-domain.example` to show `https://<slug>.your-domain.example/account`. This is configuration metadata, not DNS provisioning: the hosting operator still needs wildcard DNS, TLS, frontend wildcard hosting and appropriate backend CORS/trusted-origin settings. Tenant data always comes from the authenticated user's company; the hostname never authorizes access to another company. No DNS records or live hosting settings are changed by this code.
