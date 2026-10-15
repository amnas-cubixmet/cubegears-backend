# Platform Storage Pricing — Per GB in Credits

Admin UI: `http://localhost:8000/storage/`.

## How to configure

As a platform superuser or staff member holding both
`panel.storage.view` and `panel.storage.edit` permissions:

1. Open **Storage Management → Per-GB Storage Pricing**.
2. Set **Credits per GB**, e.g. `12.50`.
3. Set **Free Storage / Company**, e.g. `1.000` GB.
4. Choose **Daily** or **Monthly**. Monthly means one charge per
   calendar month, NOT prorated GB-days.
5. Tick **Enable storage credit billing** and save.
6. Check **Live Storage & Credit Estimate**, sourced from current
   `MediaFile.size_bytes` in each company. Older `StorageUsage` snapshots
   are shown separately for reporting, not used to create prices.

Example: 2 GiB stored, 1 free GiB, 12.50 credits / GiB / month
= **12.50 wallet credits** for that month's snapshot.

**Storage GB uses 1,073,741,824 bytes.** The platform price is in
*wallet credits* and is not itself a Razorpay INR charge. Payments to
purchase credits are handled in the existing separate Razorpay flow.

## Running a real storage charge

The form save itself never debits a wallet. On the backend:

```powershell
python manage.py migrate
python manage.py bill_storage_credits --dry-run
# Only after confirming price/tenant usage:
python manage.py bill_storage_credits
```

Schedule `bill_storage_credits` at most once daily via a single
reliable task scheduler (Windows Task Scheduler, cron or equivalent).
The command applies to active companies. On a monthly policy, runs after
the first successful monthly charge do not debit again in that month.
Daily billing charges at most once per calendar date. Re-running a
completed billing period is idempotent by CreditLedger reference.

The charge is a snapshot of currently stored `MediaFile` bytes
at run time. It is **not** historical average daily storage. New/deleted
files after a charge affect subsequent periods. This version does not
implement fractional-month proration, plan-specific free allowances,
file bytes outside `MediaFile`, automatic retries after insufficient
credits, or enforced upload limits. Do not claim otherwise.

If a wallet lacks credits, the billing engine records no debit and
returns `insufficient_credits`; administration must follow up on
that unpaid period. Configure reminders and dispute/grace policy before
production billing.

## Security / tests

Only platform personnel with explicit storage **edit** permission
can change the policy. Platform storage **view** personnel see a
read-only rate and live usage. Workshop users never have Control Panel
access, even when they hold tenant-wide wildcard permissions.

```powershell
python manage.py check
python manage.py test apps.control_panel.test_storage_per_gb_pricing apps.credits.test_metered_feature_pricing --noinput
```

No additional database migration is needed if the prior
`0004_metered_feature_and_storage_rates` migration has run.
