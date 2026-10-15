# Razorpay-hosted Buy Credits checkout (mobile and desktop)

This checkout uses Razorpay Payment Links. Customers choose a published credit package
in the Company Panel and are redirected to a Razorpay-hosted payment page.
There is **no customer-facing gateway test button**. Test versus live mode is
configured by the backend Razorpay credentials only.

## Configuration

In the private backend `.env` (never commit it):

```dotenv
RAZORPAY_KEY_ID=YOUR_ROTATED_TEST_KEY_ID
RAZORPAY_KEY_SECRET=YOUR_ROTATED_TEST_KEY_SECRET
RAZORPAY_WEBHOOK_SECRET=YOUR_PRIVATE_WEBHOOK_SECRET
FRONTEND_URL=http://localhost:5173
```

In Company Panel `.env`:

```dotenv
VITE_API_URL=http://127.0.0.1:8000/api/v1
VITE_USE_MOCK_API=false
```

For a phone test without a public deployment, both devices need network access
to the development PC. Use an appropriate reachable FRONTEND_URL, VITE_API_URL,
Django allowed hosts, and CORS origin; start Vite and Django listening on LAN.
Razorpay itself still needs internet connectivity. For public deployments,
configure HTTPS public origins and correct callback/allowed domains.

Create a `₹70.00` **published** credit package in the platform admin
`/credits/` screen. Set its credits separately. The purchase price is always
loaded from the backend database; there is no hardcoded ₹70 frontend amount.

## Sequence

1. Authenticated company billing administrator selects a package.
2. Backend POST `/api/v1/saas/credits/checkout-links` creates a CreditPurchase
   and a Razorpay Payment Link with non-partial INR pricing and an app callback.
3. Frontend redirects to the Razorpay-provided HTTPS `short_url`.
4. Customer uses Razorpay's hosted payment page; with test API keys, test
   transactions simulate success/failure without real deductions.
5. On return to `/account/credits/buy?credit_purchase=<uuid>`, frontend calls
   authenticated GET `/api/v1/saas/credits/checkout-links/<uuid>/status`.
6. Backend fetches the corresponding Payment Link AND payment from Razorpay,
   verifies provider ID, link reference, paid/captured status, INR amount and
   company-scoped purchase, then credits the wallet atomically and once.
7. Configure the webhook URL:
   `https://YOUR-PUBLIC-BACKEND/api/v1/saas/credits/webhook`
   for events **payment_link.paid** and **refund.processed**. It can settle
   credits even when the user closes the browser. Only signed webhooks work.
8. UI refreshes the company wallet balance after server confirmation.

The backend also retains its legacy order/verify endpoints for existing clients.

## Testing

```powershell
python manage.py migrate
python manage.py check_razorpay --probe
python manage.py test apps.credits.test_hosted_checkout apps.credits.test_razorpay_diagnostics
```

Never credit the wallet from the browser query parameters or from a pending /
authorized / failed transaction. Do not claim that money or credits have moved
until the server confirms a captured payment. Do not put Razorpay secrets in
React, Vite, commits, screenshots, or client-side logs.
