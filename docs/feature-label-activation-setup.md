# Enable CubeGears Paid Feature Labels (operator guide)

## Why "Activate label" is disabled

The company panel does not decide whether a credit purchase is permitted.
`GET /api/v1/saas/credits/bundles` returns `purchaseEnabled: false`
and `purchaseDisabledReason: FEATURE_ENTITLEMENTS_NOT_ENABLED`
while `CREDIT_FEATURE_ENFORCEMENT_ENABLED=False`.
Django also rejects the purchase with HTTP 409. Do not remove that server check:
charging wallets without enforcing access would be deceptive.

## Staging/local operator sequence

1. Back up your database; use a test workshop and a test wallet. Confirm your
   `.env` parses successfully, then run `python manage.py check` and
   `python manage.py migrate`.
2. Inspect the actual backend permissions without making changes:

   ```powershell
   python manage.py audit_feature_catalog
   ```

3. Register missing actual billable modules, **without enabling them**:

   ```powershell
   python manage.py audit_feature_catalog --sync
   ```

4. Log in to the **platform** Django control panel at `/features/`, review
   and publish each required `PlanFeature`. Review `/credits/` for individual
   feature pricing and wallet packages. Configure a valid bundle with at least
   two active features, a positive price, and daily/weekly/monthly/hourly/
   permanent billing. Per-use labels remain disabled until trusted backend
   operations consume the token.
5. Check every *existing* company's previously accessible modules and staff
   permissions. Backfill or communicate entitlements before introducing access
   restrictions. Enabling strict gating without backfill immediately denies
   modules that were previously accessible.
6. For **local/staging only**, when the above checks pass, set the actual
   backend environment (not the Vite `.env`) to:

   ```env
   CREDIT_FEATURE_ENFORCEMENT_ENABLED=True
   CREDIT_STRICT_MODULE_GATING_ENABLED=True
   ```

   Restart the Django process. If not ready for strict mode, leave strict
   gating disabled; note that unregistered modules can stay accessible, so it
   is not a complete paywall.
7. Sign in as a workshop owner with `billing.edit` permission and a verified
   positive credit balance. On `/account/credits/features`, refresh, check
   the quoted cost, then activate a feature label. Verify the *server* wallet
   ledger, `CompanyBundleAccess`, `/saas/credits/access`, and both company
   and staff login permissions. Another company must not see the entitlement.
8. Disable the label and verify the module becomes unavailable when no other
   entitlement covers it. Disabling a prepaid label does **not** refund credits.

## Important

The environment switches are deployment-wide, never company-level. They
must not be turned on for live customers without a staged migration, review,
and communication. Do not set them by public API or tenant UI.

For monthly postpaid features, enable the **separate** metering flags and
scheduler only after running `python manage.py audit_saas_billing --strict`.
Prepaid labels do not require the monthly meter.

`FEATURE_ENTITLEMENTS_NOT_ENABLED` = deployment switch is off;
`METERED_OPERATION_NOT_READY` = per-use label is not wired to a trusted
server operation; `FEATURE_LABEL_EMPTY` = bundle has no active modules;
`Insufficient credits` = verified company wallet lacks the required cost.
