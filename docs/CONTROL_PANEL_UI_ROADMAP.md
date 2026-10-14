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
6. [x] `/subscriptions/`, add/edit/detail — overview KPIs, company/status/cycle filters, billing and renewal forms
7. [x] `/plans/`, create/edit — pricing catalog, feature access and one-time checkout plan terms
8. [ ] `/features/`, add/edit, `/requests/` — SaaS catalog and enquiries
9. [ ] `/storage/`, `/security/` — usage and audit pages
10. [ ] `/branches/`, `/roles/` — branches and permissions
11. [ ] `/customers/`, `/vehicles/`, `/jobs/` — platform-wide workshop records
12. [ ] `/stock/` — remaining stock directory and detail/edit screens
14. [x] `/invoices/`, add/edit/detail — billing document counts, filters, invoices and estimates forms, responsive table and status displays
13. [x] `/staff/`, add/edit/detail — live counts, company/status/login filtering, responsive directory, secure invite-aware forms

## Quality requirements for each page

- Keep actual Django model/queryset data and permission checks
- Preserve CSRF on every POST and disallow unsafe GET mutations
- Use accessible labels, focus states, legible text, and responsive layouts
- No mocked financial, subscription or company counts
- Add automated rendering and business-logic regression tests
- Validate both desktop/mobile and light/dark states before merge

Status: earlier dashboard/companies/users/subscriptions work has been merged. Plan Builder payment-field follow-up is in a separate review PR.
