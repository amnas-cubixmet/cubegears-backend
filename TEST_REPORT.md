# CubixGear — Manual WhatsApp Preservation & Meta Cloud API Removal

Updated after all three implementation PR merges on 2026-11-12. This report distinguishes **GitHub CI evidence** from **deployed-browser findings**. Do not close a reported production bug without reproducing and retesting it on the affected deployment.

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

- Backend latest PR-head CI [run 43171262646](https://github.com/amnas-cubixmet/cubegears-backend/actions/runs/43171262646), commit `3cf7e6516abd26f3fb37981e7c4b07670264bb93`: **success** — check (including Django checks/migrations/regressions), PostgreSQL 16 migrations and finance tests, production Docker image build. Backend PR #80 subsequently merged into main in commit [`9e2f1a9830ea1b36ac0699b929d3c6db52f2943a`](https://github.com/amnas-cubixmet/cubegears-backend/commit/9e2f1a9830ea1b36ac0699b929d3c6db52f2943a).
- Company Panel merged-main CI [run 43170662851](https://github.com/amnas-cubixmet/company-panel/actions/runs/43170662851), merge commit `11bc866b7979157e4683c81845bcca6d0fdd2c38`: **success**.
- Customer Website merged-main CI [run 43171281680](https://github.com/amnas-cubixmet/cubegears-customers-website/actions/runs/43171281680), merge commit `b514d68949a82bb05220951d6cdad3b62cdf016b`: **success**.

- Backend CI [run 43170167792](https://github.com/amnas-cubixmet/cubegears-backend/actions/runs/43170167792), commit `2b8d8772211b8f39221ecd57d5b8458abb453fcb`: **success**. Includes Ruff, compileall, Django checks, migrations, large test suite (staff/customer/WhatsApp/finance), PostgreSQL 16 finance/migrations tests and Docker build.
- Backend prior [run 43169219378](https://github.com/amnas-cubixmet/cubegears-backend/actions/runs/43169219378), commit `743669464629b2b6ef841cf39573ea17d13f6e9c`: **success**.
- Company Panel earlier [run 43170335413](https://github.com/amnas-cubixmet/company-panel/actions/runs/43170335413), commit `b8ce3f27db0b6f0f1ad759bf84f88619222e5842`: **success**. PR #131 was merged to main and the merged-main check above subsequently passed.
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


---

# Authentication Repair — Regression and Production Readiness (2026-11-12)

**Scope:** `amnas-cubixmet/cubegears-backend` plus Company Panel and Public Website.
**Verification methodology:** real GitHub Actions on throwaway SQLite/PostgreSQL databases and
mocked email dispatch; no production accounts, live SMTP inbox, production HTTP tracebacks,
or production database records have been accessed. Production acceptance is **not established**
until real hosting and delivery checks are completed.

| Bug ID | Severity | Endpoint / component | Root cause (file) | Implemented fix | Regression / observed result |
| --- | --- | --- | --- | --- | --- |
| CG-AUTH-001 | Medium | `POST /api/v1/auth/signup` in CI | Anonymous throttling cache shared by otherwise isolated test cases (`apps/accounts/tests_part03_owner_signup.py`) | Clear test-only throttle cache in `setUp`, preserve real 5/hour throttle | Previously CI 43195421485: expected 400, got 429; run full auth suite in follow-up |
| CG-AUTH-002 | High | `POST /api/v1/auth/refresh` | `SecureTokenRefreshView` checked account status but not JWT password-hash revocation claim (`apps/accounts/views.py`) | Constant-time comparison with SimpleJWT `REVOKE_TOKEN_CLAIM` before any token rotation | Reproduced: run 43198564323 200 instead of 401 after reset. New test in `apps/accounts/tests_session_revocation.py`; follow-up CI required |
| CG-AUTH-003 | Medium | `POST /api/v1/auth/forgot-password`, `/magic-link` | Case-insensitive query `.first()` could select arbitrary legacy email-collision account (`apps/accounts/views.py`) | Reject ambiguous matches without email delivery or token issuance | `AuthenticationLifecycleTests.test_case_colliding_emails_cannot_trigger_ambiguous_recovery` |
| CG-AUTH-004 | Medium | `POST /api/v1/auth/signup` | Email-only onboarding could commit unusable account even when email dispatch disabled (`apps/accounts/views.py`) | Return `503 EMAIL_DELIVERY_UNAVAILABLE` before creating tenant; public site offers direct secure password creation | `test_disabled_email_does_not_create_magic_token_or_unusable_signup` |
| CG-AUTH-005 | Medium | `POST /api/v1/auth/setup-password` | Missing UID accessed via direct indexing, could raise 500 (`apps/accounts/views.py`) | Missing UID now returns safe HTTP 400 | `test_setup_password_without_uid_is_clean_400_not_500` |
| CG-AUTH-006 | Medium | `POST /api/v1/auth/reset-password/validate` | Reset UI did not validate link until password submission (`apps/accounts/urls.py`, `views.py`) | Add safe throttled validation endpoint and disable reset form until validated | `test_owner_email_reset_is_one_use_and_revokes_old_jwts`, `test_expired_reset_token_is_rejected` |
| CG-AUTH-007 | Low | Company Panel login/recovery frontend | Recovery had no visible manual mobile path and treated network/server errors as expired staff links (`src/pages/auth/ForgotPassword.jsx`, `StaffSetPasswordPage.jsx`) | Add manual admin-mediated mobile recovery; separate invalid link vs network/5xx; retain existing email and magic | Company Panel main CI 43199679763: lint, build, Playwright smoke passed |
| CG-AUTH-008 | Medium | Company Panel frontend regression runner | CI reported green while `node --test tests/*.test.js` executed 0 tests | Add six Node authentication error tests (`tests/auth-errors.test.js`) | In PR #154; review latest CI |
| CG-AUTH-009 | Medium | Public signup email delivery dependency | Public signup required email invitation, even if backend mail mode disabled (`app/signup/signup-form.tsx`, `app/api/signup/route.ts`) | Optional secure owner password submitted directly to Django; email invite remains supported | Public site PR #18 and main CI 43200359382 passed |
| CG-AUTH-010 | Operational | Production account email transport | Production `ACCOUNT_EMAIL_DELIVERY_MODE` defaults `disabled`; SMTP host/password/worker are environment-dependent (`apps/accounts/email.py`, `config/settings.py`) | Explicit readiness gate and existing `check_email_delivery` management command retained | Local transport mocks cover acceptance/failure; **real inbox delivery NOT VERIFIED** |

## Access and compatibility

- Existing user accounts, password hashes, tenant roles, staff records, historical data,
  customer portal tokens, and manual WhatsApp share/copy code remain intact.
- Magic login tokens are random and SHA-256 hashed, single-use; staff setup HMAC links remain manual.
  The customer website is a separate signed customer portal and was not modified.
- `SIMPLE_JWT.CHECK_REVOKE_TOKEN=True` requires both request authentication **and refresh**
  to reject old password hashes. Refresh rotation and logout use SimpleJWT blacklist.
- Mobile-only staff recovery remains **administrator-mediated manual invitation**:
  no OTP, Meta Cloud API or automatic WhatsApp provider calls.
- Email delivery acknowledgement to a public caller is intentionally non-enumerating; a queued
  Celery message does **not** certify mailbox delivery.

## GitHub changes and CI evidence

- Backend PR [#87](https://github.com/amnas-cubixmet/cubegears-backend/pull/87):
  merged to `main` as `d41d684aead72b09301aa19d349574c1c5096743`.
- Backend security PR [#88](https://github.com/amnas-cubixmet/cubegears-backend/pull/88):
  password-change refresh revocation and the expiry-test correction (merge/CI to verify).
- Company Panel PR [#153](https://github.com/amnas-cubixmet/company-panel/pull/153):
  merged as `cae57aec13f93995bcccc70a9838f18aafc1e1e9`;
  [main CI 43199679763](https://github.com/amnas-cubixmet/company-panel/actions/runs/43199679763) successful.
- Company Panel regression PR [#154](https://github.com/amnas-cubixmet/company-panel/pull/154):
  adds actual `node:test` assertions, not merely a zero-test CI pass.
- Public Website PR [#18](https://github.com/amnas-cubixmet/cubegears-public-website/pull/18):
  merged as `58960606531c6b585251054cec7f9c5be272da53`,
  [main CI 43200359382](https://github.com/amnas-cubixmet/cubegears-public-website/actions/runs/43200359382) successful.

## Remaining production release gates

1. Verify all backend and Company Panel security PR checks are green and merged;
   check the exact current main SHA after merge (a previous green SHA is insufficient).
2. Configure real HTTPS `FRONTEND_URL`, `ACCOUNT_EMAIL_DELIVERY_MODE=smtp`
   (or correctly operating Celery/Redis), provider credentials and sender **in hosting secrets only**.
   Run `python manage.py check --deploy`, migrations and
   `python manage.py check_email_delivery --to <approved-test-inbox>`.
   Confirm the actual recipient inbox/headers and email links, not just SMTP acceptance.
3. Exercise login/reset/magic/staff onboarding with **isolated staging accounts** against deployed
   domains; collect redacted response codes, `X-Request-ID`, server tracebacks (no secrets),
   refresh rotation, logout and cross-tenant denial. Do not reset real users without approval.
4. Run manually on iPhone and Android; inspect redirect URLs and no token leakage in browser
   history, logs or referrers. Check existing accounts and company RBAC are unchanged.

**Completion status:** Code fixes committed; staging and production SMTP / E2E checks are still
required before claiming full production authentication acceptance.


## Verified Vercel production deployment blockers (inspection only)

Connected Vercel team `cubixmet-s-projects` deploys from the **different** `cubixmet/*`
repositories, not the `amnas-cubixmet/*` repositories repaired above. Merging
the authentication PRs therefore does **not** automatically deploy these fixes to
the currently linked Vercel projects. Coordinate the correct production Git source
or reviewed cherry-picks before announcing production resolution.

- **CG-PROD-001, High:** Company Panel's latest inspected production
  [deployment](https://vercel.com/cubixmet-s-projects/cubegears-company-panel/ASSYqvcwNZSmo2NBLB4s6JGZTkip)
  is `ERROR`. Build logs show `MODULE_NOT_FOUND` for
  `/vercel/path0/scripts/verify-production-env.mjs` when executing
  `npm run build:production`. Repair the build script/file contract **in the
  connected deployment repository**, then deploy and verify real login.
- **CG-PROD-002, High:** Backend's latest inspected production
  [deployment](https://vercel.com/cubixmet-s-projects/cubegears-backend/HtMZJf3av69hu7jnu4WVVXRPaKrm)
  is `ERROR`; build logs show `ImproperlyConfigured: DB_ENGINE must be set
  in your private .env.` from `config/settings.py`. Configure approved
  production DB_* credentials **privately in hosting** and confirm Vercel is
  a supported runtime for the Django deployment before retesting.
- The inspected Public and Customer Website production deployments were `READY`,
  but this does **not** imply they include these latest `amnas-cubixmet`
  commits or establish any live authentication acceptance.

Do not treat failed deployment builds as a test of whether corrected login or
password reset endpoints behave properly. Verify the deployed commit SHA and
actual service HTTP responses separately.
