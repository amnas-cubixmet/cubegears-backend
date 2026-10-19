# CubixGear Dynamic SaaS Plans

## Setup

Deploy the backend and company-panel PRs together. Do not merge one in production without the other.

```bash
python manage.py migrate
python manage.py seed_saas_plans
python manage.py check
python manage.py test apps.saas.tests_account apps.saas.test_dynamic_plans --noinput
```

The seed command creates **Starter / Professional / Business** only if absent. Existing platform prices and feature selections are not overwritten.

## Platform administration

Platform staff with `panel.plans.*`, `panel.features.*`, `panel.requests.view` can use:

- `/plans/` — create/edit plan, monthly/yearly prices, promotions, limits, enabled features
- `/features/` — create/edit arbitrary label groups, nested labels and feature keys
- `/requests/` — view upgrade, downgrade and custom plan enquiries
- The former `/subscriptions/` Control Panel CRUD interface has been retired. Existing assignments and payment records are retained for compatibility and history.

The SaaS platform has no company attached. Company owners have tenant-scoped roles, **not** platform privileges.

For automatic backend checks, assign feature keys matching permission module prefixes such as `customers`, `vehicles`, `jobs`, `stock`, `staff`, `reports`, `payroll`. Nested labels are presentation groups; arbitrary new feature keys do not automatically gate unrelated views. Subpage-level feature restrictions require each corresponding API and UI to check its specific entitlement.

Always enable compliant invoice workflows for GST-registered companies. The system does not allow plan restrictions to remove invoice/GST permissions.

## Workshop account

- `/account`: company profile, usage summary
- `/account/billing`: current subscription
- `/account/plans`: published monthly/yearly plans and change enquiries
- `/account/storage`: media usage; dynamic extra GB rate when a catalog plan is assigned

The company frontend needs `VITE_API_URL` pointing to Django `/api/v1`; fake/mock plans are intentionally not seeded into browsers.

## Important production boundaries

- Plan change and custom enquiries are **requests only**. No checkout, verified paid plan activation, subscription tax invoice, recurring charge, payment webhook or auto-renewal is implemented by this PR.
- Historical note: administrators previously assigned catalog-linked subscriptions through `/subscriptions/`. That Control Panel route has been retired; do not use these legacy instructions for new onboarding.
- Limits are enforced in known branch and staff-login creation APIs for catalog-linked active subscriptions. Audit any future or alternate account creation routes.
- Feature checks are applied to matching Django role-permission modules when an **active**, catalog-linked subscription exists. Historical subscriptions without a catalog link retain previous behaviour until migrated. This migration strategy intentionally avoids unexpectedly locking existing workshops.
- Extra storage rate is an **indicative monthly estimate**, not an issued invoice. Historic snapshots do not invent charges.
- Production rollout still requires real payment integration, subscription expiry/grace automation, object storage auditing, end-to-end tests and staging acceptance before enabling automatic billing.

## Sample plan pricing

Seed defaults (editable): Starter INR 799/month, Professional INR 1,499/month, Business INR 2,999/month. Yearly 7,990 / 14,990 / 29,990. The plans include 5 / 20 / 50 GB respectively, with an indicative INR 15 per extra GB per month.

The pricing is editable in SaaS Admin. Tax calculation for issued workshop invoices follows the existing India GST engine and does not vary by SaaS tier.
