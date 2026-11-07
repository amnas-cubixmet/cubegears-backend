# CubeGears dual WhatsApp messaging modes

Each workshop chooses `manual` (default) or `automatic` through
`GET/PATCH /api/v1/whatsapp/settings`. Settings live in its own
`WhatsAppBusinessConnection` row. Migration `0002_dual_messaging_modes`
keeps existing connected-and-enabled workshops in automatic mode.

## Manual mode

No Meta credentials are required. An authorized staff user clicks **Send via
WhatsApp** from Customer Tracking, Job Card or finalized Invoice. The Company
Panel calls `POST /api/v1/whatsapp/manual-share` with `targetType` and
`targetId`. The authenticated API verifies record permissions, company
ownership, country-coded phone and active customer tracking link, then returns
a properly URL-encoded `wa.me` link and message preview. Frontend prepares a
browser tab during the user click, navigates to WhatsApp after API success,
and provides a retryable open-draft link when popups are blocked.

An entry with `manual_prepared` is recorded, **never** `sent`.
Staff must inspect the message and press Send in WhatsApp. Delivery/read
receipts are not available for manual Click-to-Chat.

Staff Password Setup uses the existing staff invitation endpoint. Authorized
administrators issue one-time, 24-hour `setupLink` then may click Send via
WhatsApp on the Staff Profile. No OTP or customer login is introduced.

## Automatic mode

Meta Embedded Signup is required per workshop. Administrators enable each
event and save its approved Meta template. Automatic sends are gated by:
- workshop mode = automatic, connected WABA, notifications enabled, event enabled;
- recorded customer opt-in or (for staff setup) employee WhatsApp consent;
- approved template and valid international recipient;
- active customer portal link;
- `WHATSAPP_SEND_ENABLED` and `CELERY_BROKER_URL`.

The worker verifies current WABA approval before sending any template. All
proactive messages use a template (not a free-form message), which also supports
communication beyond WhatsApp's 24-hour customer service window.

Logs cover `queued`, `sending`, `sent`, `delivered`, `read`,
`skipped` and `failed`; webhooks are authenticated and status updates do
not regress. Each event key is unique per company. Unknown Meta acceptance
does **not** trigger blind retries, avoiding duplicate messages. A failed or
skipped notification offers a manual fallback action in Notification History.

A staff password setup link is never stored in raw form in a Celery
argument or WhatsApp log. Do not include raw customer portal links or
staff setup links in application loggers or analytics.

## Setup

1. Apply migrations: `python manage.py migrate`.
2. For automatic mode, configure `WHATSAPP_META_APP_ID`,
   `WHATSAPP_META_APP_SECRET`, `WHATSAPP_META_CONFIG_ID`,
   `WHATSAPP_TOKEN_ENCRYPTION_KEY`, `WHATSAPP_WEBHOOK_VERIFY_TOKEN`,
   the approved frontend base URL and Celery/Redis.
3. Set `WHATSAPP_SEND_ENABLED=True` only after verifying the Meta app,
   callback signing and worker readiness. Keep tokens as deployment secrets.
4. In `/settings/whatsapp`, choose mode, connect (for automatic),
   map approved templates, set notification toggles and save.
5. Record explicit customer WhatsApp opt-in before enabling automated
   notifications. Manual sharing is still initiated by staff in WhatsApp.

## Verification

```bash
python manage.py makemigrations --check --dry-run
python manage.py check
python manage.py test apps.whatsapp.tests_integration apps.accounts.tests_mobile_staff_onboarding
```

Company Panel checks: `npm run check && npm test && npm run build`.

Real Meta credentials, signed callbacks, message delivery and phone apps
must be verified in a staging workshop separately. Tests mock official Meta
responses and do not prove live delivery.

## Part 19 — Customer Estimate WhatsApp notification and reminders

From **Job Card → Quick Estimate**, staff must **Save & Share with Customer**
to publish the newest itemized quote. The backend registers the
`estimate_shared` event **only after the database transaction commits**.
Repeated sharing of the same estimate never creates a second log. The
scheduled `estimate_reminder` event is separate, disabled by default,
and sends no earlier than the configured 24 or 48 hours after publication.

**Automatic setup:** Each workshop chooses Automatic mode, enables the
`estimate_shared` and optionally `estimate_reminder` event switches,
chooses the `estimate_reminder_hours` interval (24 or 48), and maps
approved Meta templates for both events. Templates must accept the same
three body parameters as other CubeGears updates (vehicle registration,
Job Card number, secure customer portal URL). Explicit customer WhatsApp
opt-in and valid country-coded phone remain mandatory. The platform must
enable `WHATSAPP_SEND_ENABLED=True`, deploy a Redis Celery worker, and
separately set `WHATSAPP_ESTIMATE_REMINDERS_ENABLED=True` to register
one daily 10:30 AM Beat sweep. Run **one** Beat scheduler per deployment.

**Manual fallback:** A published estimate offers **Send Estimate via
WhatsApp**, which calls the authenticated `manual-share` API with
`targetType="estimate"` and the quote's UUID. Staff sees the quote
version, amount and private portal link in a WhatsApp draft, then manually
presses Send. `manual_prepared` does not mean delivered. The API refuses
draft, superseded, withdrawn, accepted or declined quotes. Failed
estimate-specific automatic logs can only prepare a manual message while
the original estimate is still awaiting review.

**Safety and no duplication:** Each automatic event has a unique
`estimate:<uuid>:shared` or
`estimate:<uuid>:reminder:<elapsed-cadence-bucket>` key. The existing
notification-log unique constraint prevents duplicate queue records.
The Celery worker checks tenant, quote version, customer consent,
approved template, link authorization and live estimate stage again
immediately before sending. It skips a stale queued message, without
sending after a withdrawal, approval or rejection. Failed/uncertain
provider responses never cause blind retries. No raw private portal URL
is stored in notification logs.

Default configuration does not send reminders or enable WhatsApp
automatically. Deployment still requires real Meta Business verification
and a customer-consent compliance review; the CI mocks Meta calls and
does not establish live message delivery.
