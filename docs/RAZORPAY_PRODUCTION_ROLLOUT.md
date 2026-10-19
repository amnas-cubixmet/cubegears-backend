# CubeGear Razorpay SaaS billing: rollout and security checklist

Status: implementation in Draft PR. No live merchant account is connected by this repository change.

## Subscription flow

1. Company owner signs up, sets password by email and logs in.
2. When `SAAS_PAYMENT_ENFORCEMENT_ENABLED=True`, the frontend asks `GET /api/v1/saas/checkout/status`; unpaid/expired companies are redirected to `/subscribe`.
3. Owner selects a published plan and a price option. The Django backend alone reads the price and access duration from the Plan Builder.
4. Backend creates a Razorpay **Order** for the price in paise. Checkout uses the public `RAZORPAY_KEY_ID`.
5. Checkout returns payment/order IDs and a signature. Django verifies the signature using **its own stored order id**, then fetches payment from Razorpay. Only matching `captured` payment, order, currency and amount creates or renews the company's entitlement.
6. Razorpay also sends signed `payment.captured` webhooks. Webhook deliveries are de-duplicated, and capture is idempotent. `refund.processed` invalidates the last refunded active term on full refund.
7. All company users have access until subscription `renews_at`; after expiry, protected API routes return HTTP 402. Platform Admin/Django Control Panel authentication remains independent.
8. `GET /api/v1/saas/checkout/history` returns company-scoped payment records.

**This is manual renewal, not automatic recurring card/UPI mandates.** Access duration is configurable **per plan**, default 180 days (change to 210 for 7 months). The plan's **checkout_price** is a separate one-time amount for the configured term (e.g. ₹4,999 for 180 days). Legacy monthly/yearly prices remain for older plan displays but are **not used for Razorpay checkout**. Set checkout_price explicitly in the Control Panel before publishing.

## Configuration

Add secrets to your backend server's private `.env`:

```bash
RAZORPAY_KEY_ID=rzp_test_your_key
RAZORPAY_KEY_SECRET=your_private_test_key_secret
RAZORPAY_WEBHOOK_SECRET=unique_private_webhook_secret
SAAS_PAYMENT_ENFORCEMENT_ENABLED=False
```

**Never** put the Razorpay key secret or webhook secret in Vite `VITE_*` variables, commit secrets to GitHub, or let the browser choose price/duration. Public key id can be returned to Razorpay Checkout.

Enable the supported payment modes (UPI, cards, net banking) in Razorpay's merchant dashboard subject to KYC/account eligibility. Checkout will display available options. Razorpay settles money to the bank account configured in the merchant dashboard; the application does **not** request a direct bank transfer or store customers' payment credentials.

Configure **HTTPS** webhook endpoint:

`https://api.yourdomain.com/api/v1/saas/checkout/webhook`

Select events `payment.captured` and `refund.processed` (you can additionally subscribe to `payment.failed` for external monitoring). Use a new high-entropy webhook secret, separate from the API key secret.

## Test-before-live checklist

- Back up SQLite; run `python manage.py makemigrations --check --dry-run` and `python manage.py migrate`.
- Run `python manage.py test apps.saas.test_razorpay_checkout --noinput` plus existing CI suite.
- In Razorpay Test Mode, validate UPI/card/netbanking methods actually enabled, failed/abandoned payments, delayed webhooks, forged signature, wrong amount, duplicate delivery and refunds.
- Confirm all existing companies that must retain access are migrated or accommodated **before enabling** strict paywall. Existing manually created subscriptions do **not** count as Razorpay paid proof. Enabling gate immediately blocks any company without a captured order.
- Verify admin can configure both `access_duration_days` and `checkout_price` on `/plans/`. Newly created or existing plans default to checkout_price=₹0 and **cannot** create a checkout order until configured.
- Preserve and reconcile RazorpayPlanPayment records through authorized administrative reporting and Razorpay's transaction exports. The former Control Panel ledger at `/subscriptions/payments/` has been retired; the historical payment records and verification APIs remain available.
- Configure daily reminder scheduling after SMTP is verified, for example `0 9 * * * cd /srv/cubixgear/cubegears-backend && .venv/bin/python manage.py send_subscription_expiry_reminders` (adapt path and timezone to your host).
- Verify new signup → password setup → login → `/subscribe` → captured payment → dashboard.
- Verify unpaid JWT API calls return HTTP 402 even if the React UI is bypassed.
- After successful test mode rollout and company migration, configure Razorpay Live Mode credentials and separate live webhook secret, then set `SAAS_PAYMENT_ENFORCEMENT_ENABLED=True` and restart backend.
- Make backups and test restore. Do not use ephemeral storage for the SQLite database.
- Validate business KYC, settlement banking, refund/chargeback process, merchant fees, GST invoicing, privacy, customer support and legal requirements separately.

## Backend APIs

- `GET /api/v1/saas/plans`: available plan catalog and `accessDurationDays`
- `GET /api/v1/saas/checkout/status`: server-calculated entitlement, expiry, permissions and gateway readiness
- `POST /api/v1/saas/checkout/orders`: `{"plan_id": 1}`
- `POST /api/v1/saas/checkout/verify`: Razorpay callback signature and two IDs, subsequently verified from Razorpay server-side
- `GET /api/v1/saas/checkout/history`: last 100 company transactions
- `GET /api/v1/saas/checkout/orders/<order_id>`: tenant-scoped payment status and access
- `POST /api/v1/saas/checkout/webhook`: **no JWT**, verified with raw-body HMAC

All gateway calls are performed server-side via HTTPS. Merchant API keys belong to the specific Razorpay account which receives the payment.

## Current limitations before final production sign-off

- No actual live Razorpay credentials, external bank settlement, hosted production URLs, or real money verification included.
- No GST-compliant subscription tax invoice/PDF generator; transaction history is not a statutory invoice.
- Automatic recurring mandates, proration and subscription refunds initiated inside CubeGear are **not** implemented.
- Some existing subscriptions require migration planning before payment enforcement is enabled.
- Expiry warning emails are implemented via `python manage.py send_subscription_expiry_reminders` (30, 7, 1, 0 days before expiry); configure a daily cron/systemd timer and SMTP. Emails are not automatic until scheduled.
- Financial ledger reconciliation, operational monitoring, chargeback dispute handling, and legal review should be completed before launch.
