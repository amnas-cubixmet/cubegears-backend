# CubeGears Control Panel — UI alignment roadmap

Goal: Use the same visual system as `cubegears_company-panel` across the Django admin workspace, while keeping real data, RBAC and existing routes unchanged.

## Design foundation

- [x] Shared indigo/surface/border tokens and typography
- [x] Sidebar icons, collapse control, compact top bar and mobile navigation
- [x] Light, dark and system appearance
- [x] Dashboard summary cards and responsive tables

## Page-by-page rollout

1. [x] `/` Dashboard — layout, KPI cards and panels
2. [x] `/companies/` — summary cards, filters, logo avatars and actions
3. [x] `/companies/add/`, `/companies/<id>/edit/` — organized form, safe unique auto slug, image upload/preview
4. [x] `/companies/<id>/` — company profile and uploaded logo display
5. [x] `/users/`, add/edit/detail — real user KPIs, scoped search and filters, role badges, accessible user editor and security notes
6. [x] Legacy `/subscriptions/` CRUD and `/subscriptions/payments/` removed from the Control Panel; historical billing models and payment APIs retained
7. [x] `/plans/`, create/edit — pricing catalog, feature access and one-time checkout plan terms
8. [x] `/features/`, feature/group add/edit — module catalog, counts, structured feature editors
16. [ ] `/requests/` — SaaS enquiries page
9. [x] `/storage/`, `/security/` — storage snapshots and read-only audit pages
10. [x] `/branches/`, `/roles/`, add/edit/detail — directories, status filters, permission-aware editors
11. [x] `/customers/`, `/vehicles/`, add/edit/detail — customer/vehicle KPI directories and forms
15. [x] `/jobs/`, add/edit/detail — workflow counts, status/priority filters and grouped job card editor
12. [x] `/stock/`, add/edit/detail — live availability KPIs, stock-level filters, inventory details and grouped forms
14. [x] `/invoices/`, add/edit/detail — billing document counts, filters, invoices and estimates forms, responsive table and status displays
13. [x] `/staff/`, add/edit/detail — live counts, company/status/login filtering, responsive directory, secure invite-aware forms
17. [x] `/credits/` — existing credit package, feature pricing, verified purchase, wallet and ledger UI with live KPIs

## Quality requirements for each page

- Keep actual Django model/queryset data and permission checks
- Preserve CSRF on every POST and disallow unsafe GET mutations
- Use accessible labels, focus states, legible text, and responsive layouts
- No mocked financial, subscription or company counts
- Add automated rendering and business-logic regression tests
- Validate both desktop/mobile and light/dark states before merge

Status: these visual changes were committed directly to main. Full backend CI includes tests for the redesigned pages; independent payment API failures must be investigated separately.
