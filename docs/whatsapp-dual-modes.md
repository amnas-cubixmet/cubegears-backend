# CubixGear — Manual WhatsApp Sharing Only

The former automatic Meta WhatsApp Cloud API mode is discontinued. This file
documents the retained manual sharing flow and historical-data compatibility.

## Workflow

An authorized employee selects Share on WhatsApp for a customer, job, published
estimate, ready vehicle, issued invoice, or outstanding invoice payment reminder.
The Company Panel calls `POST /api/v1/whatsapp/manual-share`, using
`targetType` and `targetId`. The backend verifies organization scope and record
permissions, validates a supported international recipient, prepares a secure
customer portal URL, and returns a `https://wa.me/...?...text=...` draft.

The recipient's number is strictly validated and the message is percent-encoded.
An explicit browser click opens WhatsApp; an operator must manually press Send.
This event does not mark any message delivered/read. Historical notification
records are retained for audit compatibility.

## Targets

- `customer`: authorized customer tracking link
- `job` / `job_status`: vehicle service update
- `ready_delivery`: only if the job is Ready for Delivery
- `estimate`: only newest published reviewable estimate
- `invoice`: only finalized, non-cancelled invoices
- `payment_reminder`: only finalized invoices with outstanding balance
- `log`: authorized administrator manual recovery from legacy failed/skipped
  notification history

## Retired infrastructure

Meta onboarding, provider delivery calls, templates, webhook endpoint,
automatic retries, delivery polling and WhatsApp Celery tasks were removed.
Automatic sending has no active code path or Celery schedule. Historical
database models, tables and migrations were intentionally retained to preserve
audit records and migration compatibility. Generic Celery, billing and
auth infrastructure are unchanged.

Settings now show only manual sharing; no mode selector or connected WABA.
Customer opt-in preference records remain available. Staff invites are
issued independently of WhatsApp using expiring one-use password tokens.

## Configuration and tests

Use `FRONTEND_URL` with HTTPS in production and `CUSTOMER_PORTAL_BASE_URL`
with HTTPS as configured. No Meta provider credentials are necessary.
Backend tests: `python manage.py test apps.whatsapp.tests_integration
apps.whatsapp.tests_part19_estimate_notifications
apps.accounts.tests_mobile_staff_onboarding`.
