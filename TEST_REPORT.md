# CubixGear WhatsApp Manual-Only Migration — Test Report

Updated: 2026-11-12

## Changes
- Staff creation saves employee once and recovers safely when invitation preparation fails; independent manual invite generation via existing one-use HMAC token.
- Password setup links expire after 24 hours, are hash-checked and single-use. Production requires HTTPS FRONTEND_URL.
- WhatsApp manual draft endpoints for customers, jobs, job status, ready vehicles, estimates, issued invoices, and invoice payment reminders.
- Removed live provider connect/disconnect, template, test-send and webhook endpoints.
- Removed Meta code, automatic message triggers, and WhatsApp-specific Celery tasks; historical database models and migrations intentionally preserved.
- Inert compatibility hook for legacy estimate/job imports, never auto-send.
- Owner signup without email/password returns manual setup link once in initial signup response.

## Regression coverage
- `apps/whatsapp/tests_integration.py`: manual settings, removed provider endpoints, no queueing, phone validation, secure portal draft, tenant isolation and legacy logs.
- `apps/whatsapp/tests_part19_estimate_notifications.py`: manual estimate sharing, no automatic reminders.
- `apps/accounts/tests_mobile_staff_onboarding.py`: staff creation error resilience, no-duplicate retry, invalid production HTTPS configuration, token security, access control, retired webhook.

## Verification
- GitHub backend CI initially failed Ruff unused imports, which have since been fixed (commit bfa8ab2); subsequent CI pending.
- Browser/live WhatsApp sending, full Django suite, production traceback are not verified here.
- No production business records or old migrations deleted.

## Staff HTTP 500 diagnosis
Before migration, staff creation and invitation URL generation were coupled inside a DB transaction and manual sharing made a second invitation API call. Misconfigured link generation or provider-dependent behavior could surface an HTTP 500. Original production traceback unavailable; exact exception not confirmed. Invitation failure is now isolated at creation, and HTTP 400 explains invalid URL config during regeneration without recreating staff records.

## Delivery
Branch: `feature/manual-whatsapp-only-20261112`
PR: https://github.com/amnas-cubixmet/cubegears-backend/pull/80
Company Panel PR: https://github.com/amnas-cubixmet/company-panel/pull/131
Merge status: pending CI and integration validation.
