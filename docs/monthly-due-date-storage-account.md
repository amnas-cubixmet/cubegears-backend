# Monthly collection and owner storage account

Deploy both PRs. Run `python manage.py migrate` for credits migrations 0012–0014.

## Configure the platform

1. Credits → Monthly usage payment settings: enable collection, set the following month's collection day (default 5), set the feature observation cycle (minimum 300 seconds), and enable overdue workspace restriction. Days 29–31 clamp to the last day of shorter months. Settings are platform-only and require Credits Edit permission. Existing installations remain opt-in; no company is silently enrolled by migrating.
2. Feature Pricing: choose Monthly, enter a positive per-active-login-user rate, and select **Use now, pay next month** before publishing a new postpaid module. Existing purchased/metered modules cannot change billing model. Published prepaid packages keep immediate charges. A zero or missing price never grants a free paid module.
3. Storage: publish a positive credits/GB monthly rate, free GB allowance and observation interval. With the newer monthly storage engine enabled, the form selects time-weighted monthly usage. Uploaded/deleted file events and the stored usage periods are separate records: deleting a file reduces future occupancy and preserves prior accrued charges.

For the newer monthly feature/storage engines, configure the existing environment switches:

```
CREDIT_BILLING_CELERY_ENABLED=True
CREDIT_FEATURE_ENFORCEMENT_ENABLED=True
CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED=True
STORAGE_MONTHLY_METERED_ENABLED=True
CREDIT_POSTPAID_FEATURE_BILLING_ENABLED=False
CREDIT_LABEL_ONLY_ACCESS_ENABLED=False
```

Use only one feature engine and one storage engine. The new storage engine uses a monthly Storage policy, represented internally by billing_mode=snapshot; its UI label changes to time-weighted monthly usage when enabled. Do not also enable the legacy metered_monthly storage policy. Without due-date collection, STORAGE_METER_INTERVAL_SECONDS remains the existing observation-gap tolerance. With collection enabled, feature cycles use Credits → Feature observation cycle and storage cycles use Storage → Meter interval. Samples wait until their configured cycle; file mutations and feature deactivation flush the final observed interval immediately. Gaps larger than twice the configured cycle remain unbilled. The storage sampler runs every minute, feature sampler every five minutes, and due-date collection retries hourly at minute 35. Keep one Celery Beat scheduler plus workers running. No DNS, payment credentials, running workers or deployed environment settings are changed by this PR.

## Monthly lifecycle

Usage closes at calendar month-end. Collection attempts before the configured next-month day return `not_due`, without debiting credits. The first settlement attempt freezes that month's due date for all companies; later policy edits apply to unissued months. GET requests never create deadlines, settle bills or modify wallets.

Example: October usage is collected from November 5 in Django TIME_ZONE. Insufficient funds remain outstanding, with no negative wallet balance. Access continues through November 5 and is restricted from November 6 at local midnight if a positive closed-month debt remains. Zero balance alone, open-month accrual or a zero-value bill does not trigger restriction. The gate applies to free modules as well as paid modules, server APIs and direct frontend navigation.

Owners retain SaaS Account, Buy Credits, feature/billing logs and read-only Storage usage/events/history. Staff cannot enter owner billing or storage recovery views; they see a payment-overdue notice. Staff access responses reveal the restriction state, not owner financial amounts. Upload, delete, file download and storage policy mutations still require operational permissions and remain blocked while overdue.

Verified Razorpay Checkout, hosted-payment-link credits and positive audited platform adjustments immediately retry outstanding bills through the existing atomic settlement functions. Older unpaid months are retried too. Each category/feature/period retains its existing unique ledger reference, so repeated notifications or retries do not debit twice. A collection failure never rolls back a verified captured payment. The hourly retry handles temporary collection failures. Feature metering pauses while overdue debt locks the company; once all outstanding usage is settled, enabled valid postpaid features resume from the recovery timestamp, without back-billing the locked interval. Storage occupancy remains measurable while files are stored.

The original legacy monthly sessions/intervals remain supported by the same due calendar. Only the selected engine contributes debt; prepaid and snapshot charges are not converted to postpaid by this policy.

## Storage evidence

The owner Storage tab is available without buying a paid Storage module. It calls only storage/account read endpoints, avoiding unrelated Settings requests. The read-only policy summary displays the actual per-GB rate, free allowance and interval. Events show file name, action, actor, time and byte delta, with pagination for additional records. Daily history and file events are audit evidence; invoice amounts come from stored usage accruals, never a sum of daily peak snapshots. Unknown worker gaps remain unbilled under the existing conservative metering rules. File operations also observe usage before and after mutation to preserve the occupied interval and update future occupancy immediately.

Read-only storage does not grant upload/delete/settings/file-download permission. Operational file controls remain disabled unless storage.manage is authorized. The platform `/storage/` view already lists the tenant-filtered immutable file events, including deleted-file names.
