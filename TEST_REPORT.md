# CubixGear WhatsApp Manual-Only Migration — Test Report

Updated: 2026-11-12

## Changes
- Staff creation saves an independent user and employee, and recovers safely if invitation setup fails.
- Password invitations stay expiring, one-use and hash-checked.
- WhatsApp click-to-chat URLs remain manual and workshop-scoped.
- Removed provider connect, disconnect, template, test-send and webhook API routes.
- Removed Meta code, automatic messaging signals and WhatsApp-specific Celery tasks; historical database models and migrations are intentionally preserved.
- Automatic event enqueueing is an inert compatibility hook to avoid breaking existing job/estimate imports.
- Owner signup without an email/password now returns a one-time manual setup link in its creation response.

## Regression tests
- `apps/whatsapp/tests_integration.py`: manual-only settings, removed endpoints, no queueing, phone validation, private portal draft, tenant isolation and legacy logs.
- `apps/whatsapp/tests_part19_estimate_notifications.py`: manual estimate sharing and suppression of automatic reminders.
- `apps/accounts/tests_mobile_staff_onboarding.py`: no-consent manual invitation, staff creation resilience, unauthorized/expired/used tokens and webhook removal.

## Verification
Not yet run in this environment. GitHub Actions checks will be consulted before any merge.
Local Django checks, database migrations and full integration/browser tests remain to be executed.
No production data or migrations have been deleted.

## Root-cause investigation
The existing creation flow issued an invitation inside the transaction and the frontend triggered a second invite request during manual share. Previously, server-side automatic delivery configuration could participate in that call. A production traceback is not available, so the exact original HTTP 500 exception remains unverified. The revised code eliminates the provider dependency and contains invitation failures without recreating a staff member.

## Delivery
Branch: `feature/manual-whatsapp-only-20261112`
Status: proposed; not merged.
