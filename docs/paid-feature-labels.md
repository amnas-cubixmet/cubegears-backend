# Paid Feature Labels (existing modules, credit wallet)

The platform `/features/` page lets an authorized CubeGear administrator combine
**at least two existing PlanFeature records** into one paid label. A label has:

- A stable server-generated key, name, description and chosen existing modules.
- A **credit** price (not an INR amount); funds are added through the existing
  verified Razorpay credit-top-up flow.
- One billing term: **permanent**, **weekly** (7 days), **daily** (24 hours),
  **hourly** (1 hour), or **per-use** (reserved for instrumented server actions).

Company billing admins can discover labels at
`GET /api/v1/saas/credits/bundles`, purchase via
`POST /api/v1/saas/credits/bundles/purchase` with `{"bundleId": 1}`,
and disable via `POST /api/v1/saas/credits/bundles/disable`.
The frontend UI is **Company Panel → Account → Feature Controls**.

## Security and behavior

- Price, modules and term are calculated by the Django server. No browser amount
  or role supplied with the request is honored.
- Purchases require `billing.edit`, sufficient company wallet credits, a
  published label and an enforced feature access policy.
- Set `CREDIT_FEATURE_ENFORCEMENT_ENABLED=True` in the **backend-only** `.env`
  **after** auditing and migrating existing company entitlements in staging.
  With this flag disabled, label purchases are blocked and the UI explains why.
- Existing tenant RBAC remains mandatory. A label only adds a paid entitlement,
  never permission to perform actions the role does not allow.
- Multiple companies have entirely separate access and wallet balances.
- Term expiration is checked at request authorization, without waiting for a
  scheduler. Renew by purchasing again; existing nonexpired timed entitlements
  extend their expiration. No automatic wallet renewal or surprise deductions.
- Permanent labels are not charged again while active. Already-purchased
  permanent access is protected if catalog terms change programmatically.
- Once a label has purchasers, the admin form does not permit removing its
  existing features or changing its billing term. Create a new label instead.
  Adding a new included feature changes the entitlements for current purchasers.
- Disabling a label removes access without refunding already-spent credits.
  Unpublishing a label suspends its associated access.
- Existing individual 30-day credit features continue operating unchanged.

## Per-use support (not enabled for purchases)

An arbitrary API read or button click is **not** a trustworthy billable usage
event. Per-use is configurable in the catalog but its purchase endpoint returns
`409 METERED_OPERATION_NOT_READY` until a specific backend operation is
integrated. This prevents customers being charged for unusable usage tokens.

Trusted feature implementations may use the atomic internal service hook
`apps.credits.service.consume_bundle_use(company, bundle, feature,
operation_ref=..., actor=...)`. Invoke it inside the same Django
`transaction.atomic()` as the *successful business operation*, with a stable,
unique operation reference and normal role authorization. Duplicate references
are idempotent. Never call this hook from a client-reported usage counter or
expose it directly to the browser. Once real operations are instrumented and
tested, enable per-use purchasing for only those mapped features.

## Local verification

```powershell
python manage.py makemigrations --check --dry-run
python manage.py migrate
python manage.py test apps.credits.test_feature_bundles --noinput
```

The frontend GitHub Actions workflow also tests bundle API wiring and builds
the Company Panel. **Do not enable real charges in production until staging
tests cover your particular feature and role combinations.**
