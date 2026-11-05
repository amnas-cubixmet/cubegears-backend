# CubeGears Workshop Workflow Simplification — Verified Code Audit and Delivery Gates

Date: 2026-11-05. Scope: existing Django backend and React Company Panel, no rebuild.

## Existing functionality to preserve

| Area | Existing implementation | Verification/gap |
|---|---|---|
| Services | `apps/services/models.py` ServiceCategory.parent, Service, validated tenant-scoped hierarchy; `apps/services/tests_hierarchy.py` | Already supports nested categories; do not recreate a category catalogue. |
| Job Estimate | `apps/jobs/views.py`, `apps/jobs/india_gst.py` | Explicit draft approval/skip and HSN-classified GST; never auto-approve or infer zero GST from an item name. |
| Work | `Job.work`, payroll timer sessions, outside labour API | Existing worker assignment/timers remain. Added `POST /api/v1/jobs/{id}/work/services` as a first action for selected service; billed customer charge is different from *unposted* agreed worker charge. |
| Stock | `StockItem`, `StockMovement`, `JobPart`, issue API | Quote != stock issue; reuse atomic stock movements for actual issue. |
| Complaints | `apps/jobs/complaint_status.py`, `apps/jobs/tests_manual_complaints.py` | Staff-manual statuses and audit present; never auto-resolve at Work/QC completion. |
| Quality Check | `apps/jobs/views.py` workflow/QC | Fail and safety requirements still mandatory. |
| Invoice suggestion | `apps/invoices/job_draft.py`, `InvoiceViewSet.job_draft` | Actual billable work, issued stock, outside billable purchases; read-only suggestion, not finalized invoice or automatic stock deduction. |
| Customer payments | `apps/payments/models.py`, `views.py` | Invoice balance locking and overpayment guard; not yet a unified Cash/Bank ledger. |
| Expenses | `apps/expenses/models.py` | Has expense entries; no shared payment-account posting infrastructure. |
| Suppliers | `apps/inventory/models.py` Supplier | Contact/address/GSTIN only; no supplier bill/payment allocation, payable ledger or credits. |

## Delivery in controlled phases

1. **Work Service Quick Entry (this PR)** — catalogue selection, optional mechanic, separate customer charge and unposted agreed worker charge. Stable request ID prevents duplicate jobs on retry. Audit event is company-scoped. It does *not* pay staff, modify stock, create an expense or finalize an invoice.
2. **Work/Part/Outside Purchase UI** — present four primary actions, using existing issue-part and outside-parts flows. Differentiate quote, issue, receipt and direct-job outside purchase. Do not blend direct cost with selling rate.
3. **Supplier purchases / liabilities** — add `SupplierBill`, `SupplierPayment`, `SupplierPaymentAllocation` with immutable financial history, company isolation, due dates, unique source/idempotency keys. Post payable on approved/received bill only. Payment reduces payable, never repeats the purchasing expense.
4. **Cash/Bank accounts / journal** — introduce company-owned `PaymentAccount`, append-only `AccountEntry` with unique source+event, confirmed transfers as paired debit/credit in one transaction, explicit reconciliation and reversal records. Mark internal recorded balances as such; no claim of live bank access.
5. **Inventory quick purchase** — link goods receipt to purchase bill and supplier ledger; never receive the same goods twice. Permit partial credit and paid purchase; distinct receipts, bills and payment stages.
6. **Invoice draft and end-to-end** — load existing suggestions on Invoice screen without side effects; preserve actual billable service selling rates and separate worker charges; never overwrite approved snapshots. Validate tax on server; retain draft/finalize transitions.
7. **Financial reconciliation/QC** — enforce complaint decision before QC using explicit policy, no automatic resolved status; test full flow including stock concurrency, supplier partial payment allocations, cash vs bank, refunds, audit reversals, two tenants and mobile viewports.

## Non-negotiable testing gates

- `python manage.py makemigrations --check --dry-run`, `python manage.py migrate`, `python manage.py check`, backend regression suite
- `npm run check`, `npm test`, `npm run build`, `npm run test:ui`
- Two requests with the same idempotency ID create exactly one service and one audit event.
- Source account debited once for payment; supplier payable reduced once; no repeated expenses.
- Job stock issue is not repeated by invoice creation.
- No unauthorized cross-company record selection.
- Skipped or incomplete QC does not silently create a finalized invoice.
- Explicit tax treatment only: never assume exemptions.

**Status:** The ledger/supplier purchasing steps above are **not implemented by this PR**. Each requires its own migration, tested financial transaction boundary and independent release. Avoid declaring the full master workflow production-ready until those gates pass.
