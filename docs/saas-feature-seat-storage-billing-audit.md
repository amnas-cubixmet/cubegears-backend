# CubeGear Feature, Seat and Storage Billing Audit

## Implemented module discovery

Run `python manage.py audit_feature_catalog` to inspect the backend's
authoritative tenant permissions and model-to-module mappings. The JSON report
shows each actual module, known permission keys, backing model names, category,
whether it may be priced, existing registered keys, and missing keys.

Run `python manage.py audit_feature_catalog --sync` to create **disabled**
`PlanFeature` records for missing real billable modules. Existing labels and
active states remain unchanged. Staff must review and enable each entry.

The tool deliberately does **not** scan arbitrary React buttons and present
them as purchasable modules. A backend permission + executable API is required.
Keep account, credit purchase, authentication, settings, notifications and
storage usage readouts accessible rather than creating circular paywalls.

## Existing system verified in repository

- Tenant RBAC: `apps/accounts/permissions.py`
- Authority/catalog: `apps/roles/permission_catalog.py`
- Company-wide paid entitlements: `apps/saas/access.py`
- Multi-feature bundles: `apps/credits/bundle_views.py` and
  `apps/credits/service.py`
- Individual feature base/per-staff **30-day** billing:
  `FeatureCreditPrice`, `CompanyCreditFeature`
- Credit wallet and idempotent ledger: `apps/credits/models.py`
- Actual media storage event history: `apps/saas/storage_ledger.py`

## Design required before enabling additional charges

1. **Features:** Give every module a stable permission key; admin may combine
   one or several into a priced label, choose permanent, weekly, daily, hourly,
   or a separately instrumented per-use unit. Company entitlement is shared,
   but every staff user still needs role permission. Do not grant wildcard RBAC.
2. **Seats/users:** Bill active login accounts `accounts.User` (not
   `employees.Employee`). Store the billable quantity snapshot, tier,
   renewal window and included seats. Add a separate per-active-user rate
   in credits; do not conflate with module enablement charges.
3. **Storage:** Meter persisted bytes from `MediaFile` and the storage event
   ledger; compute daily GB-days or other explicit published unit, free
   allowance, rounding and retention, and an auditable separate charge.
   Never bill based on browser-reported files.
4. **Recurring billing:** Create an idempotent charge-period key for company,
   rate/version, billing unit, interval start and end. Perform wallet debit
   and subscription-state mutation within one atomic database transaction.
   Scheduler must be safe to retry and catch up without double debiting.
5. **Limits:** Define insufficient-balance grace/suspension policy,
   admin notifications, rollback/refund policy, proration and whether
   currently purchased paid packages retain their original feature set.
6. **Money vs credits:** Razorpay payments purchase wallet credits only
   after captured amount/order/signature confirmation. Display INR top-up
   prices separately from feature credit prices. No frontend credit mutation.
7. **Rollout:** Audit current tenant permissions/paid subscriptions and seed
   historical entitlements before enabling `CREDIT_FEATURE_ENFORCEMENT_ENABLED`.
   Existing users must not lose functionality without a migration and notice.

This PR implements inventory only, not new production charges.
