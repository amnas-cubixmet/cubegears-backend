# CubixGear — Manual WhatsApp Preservation & Meta Cloud API Removal

Verified on 2026-11-12. This report distinguishes **GitHub CI evidence** from **deployed-browser findings**. Do not close a reported production bug without reproducing and retesting it on the affected deployment.

## Scope and compatibility

- Automatic Meta Graph API calls, provider connect/disconnect, template management, webhooks, auto sends, WhatsApp Celery tasks, event-triggered notifications, and estimate reminder scheduler have been retired.
- Historical WhatsApp tables and migrations are preserved; shared Redis/Celery for unrelated accounting and notifications remains unchanged.
- The independent `apps/accounts/staff_setup.py` token service issues one-use, 24-hour HMAC-validated setup links; production requires HTTPS company-panel `FRONTEND_URL`.
- `apps/customers/portal_tokens.py` provides tenant-scoped revocable/rotatable customer portal links, independent of Meta.
- `apps/whatsapp/manual.py` validates permission, company, record, country-coded number, publication state, and customer access, then returns a percent-encoded `https://wa.me/{phone}?text=...` draft. A human must press Send.
- Manual targets: customer, job, job_status, ready_delivery, estimate, invoice, payment_reminder, legacy failed/skipped log. No draft is marked as delivered/read.
- Staff: Create → secure invitation → Share / Copy → regenerate existing staff ID. Company Panel error handling keeps loaded profile visible on optional-data failures, shields raw HTML and offers Retry.

## Bug register

| ID | Endpoint and reported status | Root-cause evidence / file | Fix | Automated retest | Final status |
|---|---|---|---|---|---|
| CG-STAFF-001 | POST `/api/v1/employees/{uuid}/invite`, reported 500 after POST `/api/v1/employees` succeeded | Exact deployed 500 traceback absent. CI demonstrated an unhandled `ValueError` for unsafe `FRONTEND_URL` from `apps/accounts/staff_setup.py:32`, via `apps/employees/views.py` invitation. | Staff creation transaction savepoint/failure recovery; retry without duplicate; HTTPS validation with local HTTP exception; manual-only generation | `apps.accounts.tests_mobile_staff_onboarding` included in successful backend CI | CI verified; production/browser retest pending |
| CG-STAFF-002 | Staff Profile initial GET `/api/v1/employees/{uuid}` or `/api/v1/employees/me`, reported 500 with raw HTML; **which precise browser request failed has not been captured** | No original browser Network export or Django traceback. Optional secondary requests: teams, shifts, skills, documents, activities. | Backend staff detail/activities regression; frontend `safeApiErrorMessage` and `Promise.allSettled` support requests, retry and 401/403/404/500 handling | `test_staff_profile_detail_and_activity_support_endpoints_are_json_not_500` and Company Panel build/regressions passed | Production failure source and live retest pending; do not mark fixed |
| CG-CUSTOMER-001 | GET/POST `/api/v1/customer-portal/links/{uuid}`, POST `/api/v1/whatsapp/manual-share`; user reported error, **exact failing URL/status unknown** | Reproducible stale digest -> 409 with regeneration path; invalid ID -> 400; tests cover tenant isolation. Original deployed error not traced. | Customer portal stale link regeneration and frontend retry; strictly validated phone and tenant-scoped manually prepared drafts | `apps.customers.tests_portal_access`, `apps.whatsapp.tests_integration`, Customer Website node tests passed | CI verified; original browser request and live retest pending |

Issues: [CG-STAFF-001 #82](https://github.com/amnas-cubixmet/cubegears-backend/issues/82), [CG-STAFF-002 #83](https://github.com/amnas-cubixmet/cubegears-backend/issues/83), [CG-CUSTOMER-001 #84](https://github.com/amnas-cubixmet/cubegears-backend/issues/84).

## Verified GitHub Actions evidence

- Backend CI [run 43170167792](https://github.com/amnas-cubixmet/cubegears-backend/actions/runs/43170167792), commit `2b8d8772211b8f39221ecd57d5b8458abb453fcb`: **success**. Includes Ruff, compileall, Django checks, migrations, large test suite (staff/customer/WhatsApp/finance), PostgreSQL 16 finance/migrations tests and Docker build.
- Backend prior [run 43169219378](https://github.com/amnas-cubixmet/cubegears-backend/actions/runs/43169219378), commit `743669464629b2b6ef841cf39573ea17d13f6e9c`: **success**.
- Company Panel [run 43170335413](https://github.com/amnas-cubixmet/company-panel/actions/runs/43170335413), commit `b8ce3f27db0b6f0f1ad759bf84f88619222e5842`: **success**. PR #131 was merged to main; subsequent change commit `1c3e9358fcf38f693330d0695f631569863632f8` also needs final CI visibility.
- Customer Website [run 43169740480](https://github.com/amnas-cubixmet/cubegears-customers-website/actions/runs/43169740480), commit `aca13b6b40781ea70dca36cf0adecfd76c2c11e0`: **success** (Node tests and build).

## Owner signup safety and verification

Without the retired automatic platform WhatsApp sender, public signup cannot deliver an invitation to a phone-only, passwordless owner securely. The API now requires **an email address or a password** before creating that workshop; it does not return an owner password bearer token to anonymous callers. The Public Website already asks for email and was not changed.

`apps.accounts.tests_part03_owner_signup` was added to Backend CI; the full PR head check [43171262646](https://github.com/amnas-cubixmet/cubegears-backend/actions/runs/43171262646) passed. No live email transport or owner inbox receipt was verified.

## Explicitly not verified

- No access to the user's localhost browser Network panel or original Django server traceback; exact failing deployed endpoint and 500 stack for CG-STAFF-002 are still unknown.
- No real WhatsApp mobile/desktop Send interaction or permissioned end-to-end browser session was executed from GitHub connector. CI tests validate draft construction but **not delivery**.
- All 15 requested workflows were not exercised end-to-end in a live workshop; production acceptance is not declared.
- No automatic WhatsApp provider traffic is expected from application code; production infrastructure/provider logs have not been inspected.

## Delivery references

- Backend PR [#80](https://github.com/amnas-cubixmet/cubegears-backend/pull/80): **merged to main** at `9e2f1a9830ea1b36ac0699b929d3c6db52f2943a`. PR head `3cf7e6516abd26f3fb37981e7c4b07670264bb93` passed [Backend CI run 43171262646](https://github.com/amnas-cubixmet/cubegears-backend/actions/runs/43171262646), including owner signup security regressions.
- Company Panel PR [#131](https://github.com/amnas-cubixmet/company-panel/pull/131): **merged to main** at `11bc866b7979157e4683c81845bcca6d0fdd2c38`. Main CI [43170897499](https://github.com/amnas-cubixmet/company-panel/actions/runs/43170897499) and Supplier Validation [43170897507](https://github.com/amnas-cubixmet/company-panel/actions/runs/43170897507) passed; updated frontend manual-share, error-handling and UI tests included.
- Customer Website PR [#33](https://github.com/amnas-cubixmet/cubegears-customers-website/pull/33): **merged to main** at `b514d68949a82bb05220951d6cdad3b62cdf016b`. Main CI [43171281680](https://github.com/amnas-cubixmet/cubegears-customers-website/actions/runs/43171281680) passed.
- Public Website: reviewed; no automatic Meta sender found, no code change necessary for manual sharing.

## Release gates

Capture redacted browser Network request IDs + Django traceback on affected deployment for CG-STAFF-001/002/CG-CUSTOMER-001. Verify 401/403/404/500 behaviors; manually test iPhone/Android and desktop, expired token, no duplicate creation, copy, invoice/estimate/reminder/ready, cross-tenant rejection, and absence of outbound Meta provider HTTP requests. Close issues only after these gates pass.
