# CubeGear feature discovery and credit metering

The feature catalog comes from `apps.roles.permission_catalog.TENANT_PERMISSIONS`.
It is the backend's actual authorization registry, not a list of guessed UI pages.
Do not infer sellable modules from React components or arbitrary URL strings.
The platform panel and tenant core modules are excluded from paid feature
sync: dashboard, company, billing, storage, settings, notifications, roles.

## Platform setup

1. Run `python manage.py migrate`.
2. Sign in as an authorized **platform admin** and open `/features/`.
3. Review the read-only "Discovered Module Inventory", then press **Sync Missing Modules**.
   Only missing `PlanFeature` rows are added. Nothing is repriced, purchased or
   enabled for tenants. Existing names, features and active states are preserved.
4. At `/credits/`, set per-module base credits, first activation charge,
   price per **active login user**, legacy price per **active Employee**,
   and billing interval (daily, weekly, 30-day).
5. Optionally group 2+ existing modules under **Create Paid Label**, with a
   monthly / weekly / daily / hourly / permanent duration and optional
   activation / per-active-login-user price. An admin can see bundle pricing
   in the company feature marketplace.
6. Enable `CREDIT_FEATURE_ENFORCEMENT_ENABLED` only after reviewing the
   catalog, default entitlements and access to core/essential modules.
   Feature purchases apply to the **company**, not one individual.
   Each user's existing RBAC permissions still decide which actions they may perform.

## Billing calculation

An individual feature's first charge is:
`activation_credits + base_credits + per_user_credits × active_company_logins + per_staff_credits × active_company_employees`.

Subsequent renewal charge drops the activation component. Exactly one company
wallet is debited under a unique ledger reference. Daily/weekly/30-day
periods are elapsed durations starting at purchase, not calendar boundaries.
Enabled features expire at `next_charge_at` until the scheduled charge job
successfully renews them. Disable prevents future renewals, without refund.

Bundled labels are **prepaid**, manually extendible (no surprise auto-renewals).
They charge `price_credits + per_user_credits × active_company_logins`,
plus activation credits on the first purchase or reactivation. Existing
role permissions remain mandatory. Newly added seats are reflected at the
next payment/renewal; immediate per-seat proration is not provided.

## Scheduling

For paid single-feature renewal, run **one shared worker** at least daily,
or more often if you sell hourly duration elsewhere:

```powershell
python manage.py charge_credit_features --dry-run
python manage.py charge_credit_features
```

For storage, the platform configures a separate **disabled-by-default** policy
at `/credits/`. Charges use the current database MediaFile size total,
not browser-reported usage. Include X GB free, then charge the configured
credits per extra GB per calendar day or month. Usage is snapshotted at
the job's run time: it is NOT prorated byte-hours or retrospective daily
peak usage. A ledger entry, including a zero-credit entry if under quota,
locks one charge per company/period. Insufficient wallet balance is reported
and not charged; storage access is **not** automatically suspended.

```powershell
python manage.py bill_storage_credits --dry-run
python manage.py bill_storage_credits
```

Schedule storage billing once daily from **one** worker after enabling the
policy. Existing storage subscription limits and INR prices stay separate;
don't enable overlapping storage charges without reviewing the business model.

## Verification

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test apps.credits.test_metered_feature_pricing --noinput
python manage.py test apps.credits.test_feature_bundles --noinput
```

Never debit the wallet from frontend JavaScript. Wallet topups still depend
on independent verified Razorpay payment captures.
