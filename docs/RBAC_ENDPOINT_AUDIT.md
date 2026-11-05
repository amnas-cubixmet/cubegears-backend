# CubeGears tenant RBAC — permission-to-endpoint audit

**Reviewed:** 2026-11-05. **Scope:** source-level review of Django and React modules listed below. An authorization **matrix**, not evidence that every route was integration-tested under every role. Default deny is enforced by `apps.accounts.permissions.RolePermission`; React guards only improve user experience.

## Authorization layers

Every protected API operation must pass: authenticated and active user; active tenant/company; role permission; paid feature entitlement and billing state; authorized branch and record; business validation; explicit financial approval policy. Verified owner receives tenant role permissions but never bypasses feature entitlement, billing holds or tenant record boundaries. Platform superusers do not have implicit tenant permissions.

**Rollout order:** backend code -> role data migration -> backend regression/negative tests -> frontend -> staging manual checks -> production. Existing Role JSON permissions are **appended**, not replaced. Do **not** expose new frontend action controls before the backend knows their permission codes and migration has run. Keep unmerged while security tests/review are outstanding.

## Permission-to-endpoint matrix (backend)

Notation: `+ ` means **all** listed permissions are required, not either permission. `/{id}` indicates URL parameters; router paths have optional trailing-slash compatibility. All endpoints are under `/api/v1`.

| Method / endpoint | Required capability | Source / notes |
|---|---|---|
| GET `/roles/`, `/roles/{id}` | Verified company admin; staff assignable lookup uses `staff.create` or `staff.edit` | `apps/roles/views.py` |
| POST/PATCH/DELETE `/roles/*` | Verified **company owner** + `company.manage` | System roles and assigned-role deletion protected |
| GET `/saas/credits/access` | Authenticated active company | Returns server-filtered `TENANT_PERMISSIONS`, no grant mutation |
| GET/POST `/attendance/status`, `/attendance/toggle` | `attendance.self` | User's own employee, company rules applied |
| GET `/my-attendance/logs`, `/my-attendance/calendar`, `/leave/balances` | `attendance.self` | Self only |
| POST `/my-attendance/corrections`, `/leave/requests`, `/my-attendance/overtime` | `attendance.self` | Staff-submitted requests; server validates identity/rules |
| POST `/leave/requests/{id}/cancel`, `/my-attendance/overtime/{id}/cancel` | `attendance.self` | Own request |
| GET `/attendance-manager/approvals`, `/attendance-manager/team`, `/attendance-manager/master`, `/attendance-manager/calendar` | `attendance.team.view` | Branch-scoped staff/records in updated code |
| GET `/attendance-manager/staff-details/{staff}/{date}` | `attendance.team.view` | Branch-scoped |
| POST `/attendance-manager/approvals/{id}` for Punch Correction | `attendance.team.view` + `attendance.corrections.approve` | Same-branch, self-review policy |
| POST `/attendance-manager/approvals/{id}` for Leave | `attendance.team.view` + `attendance.leave.approve` | Same-branch, self-review policy |
| POST `/attendance-manager/approvals/{id}` for Overtime | `attendance.team.view` + `attendance.overtime.approve` | Same-branch, self-review policy |
| POST `/attendance-manager/team`, PUT `/attendance-manager/team/{id}` | `attendance.records.manage` | Branch scoped |
| GET `/attendance/records`, `/attendance/leave`, `/attendance/overtime` | `attendance.team.view` | Branch scoped |
| POST/PUT/PATCH/DELETE `/attendance/records`, `/attendance/leave`, `/attendance/overtime` | `attendance.records.manage` | Tenant checks + branch checks |
| POST `/attendance/overtime/{id}/approve`, `/reject` | `attendance.overtime.approve` | Self-review restricted |
| GET `/attendance-manager/shifts`, `/rules`, `/holidays` | `attendance.team.view` | Rules and holidays read only |
| POST `/attendance-manager/shifts` | `attendance.shifts.manage` | Shift assignment/configuration |
| POST `/attendance-manager/rules` | `attendance.rules.manage` | Company attendance rules |
| POST `/attendance-manager/holidays`, DELETE `/attendance-manager/holidays/{id}` | `attendance.holidays.manage` | Holiday mutation |
| GET `/attendance/rules`, `/attendance/holidays` | `attendance.team.view` | Company scoped |
| POST/PUT/PATCH/DELETE `/attendance/rules`, `/attendance/holidays` | `attendance.rules.manage` / `attendance.holidays.manage` | As appropriate by resource |
| GET `/payroll/daily-wages/`, `/history`, `/employees/{id}` | `payroll.view` | Employee branch-scoped; personal payment list removed from account response without payment-history grant |
| GET `/payroll/daily-wages/payments`, `/payroll/salary-payments` | `payroll.payments.view` | Payment history separate |
| POST `/payroll/daily-wages/employees/{id}/rates` | `payroll.rates.manage` | Rate change |
| POST `/payroll/daily-wages/employees/{id}/finalize` | `payroll.wages.finalize` | Day finalization |
| POST `/payroll/daily-wages/employees/{id}/extras` | `payroll.extras.manage` | Extra earning request |
| POST `/payroll/daily-wages/extras/{id}/approve` | `payroll.extras.approve` | Review pending extra |
| POST `/payroll/daily-wages/employees/{id}/adjustments` | `payroll.adjustments.manage` | Audited adjustment |
| POST `/payroll/daily-wages/employees/{id}/pay` | `payroll.payments.record` | Wage ledger full/partial payment |
| POST `/payroll/daily-wages/payments/{id}/reverse` | `payroll.payments.reverse` | Requires reason; audited reversal |
| POST `/payroll/runs/{id}/process`, `/submit` | `payroll.process` | **Historical route currently GET-only**; action unavailable |
| POST `/payroll/runs/{id}/approve` | `payroll.approve` | **Historical route currently GET-only**; action unavailable |
| POST `/payroll/payslips/{id}/payment` | `payroll.payments.record` | **Historical route currently GET-only**; action unavailable |
| POST `/payroll/adjustments/{id}/approve`, `/reject` | `payroll.adjustments.manage` | **Historical route currently GET-only**; action unavailable |
| GET/POST/PATCH/DELETE `/payments` | `payments.view/create/edit/delete` respectively | **Existing unsafe update/delete behavior requires follow-up**, see below |
| GET/POST/PATCH/DELETE `/expenses` | `expenses.view/create/edit/delete` respectively | Supplier allocation rows locked against update/delete |
| GET `/inventory/procurement/bills` | `stock.view` + `payments.view` | Supplier financial invoice |
| POST `/inventory/procurement/bills` | `stock.create` | Draft supplier bill, not bank settlement |
| POST `/inventory/procurement/bills/{id}/post` | `stock.edit` + `payments.create` | Liability / optional inventory receipt |
| GET `/inventory/procurement/payments` | `stock.view` + `payments.view` | Supplier settlement records |
| POST `/inventory/procurement/payments` | `stock.view` + `payments.create` | Bank ledger entry and allocations |
| POST `/inventory/procurement/payments/{id}/allocate` | `stock.view` + `payments.edit` | Advance allocation, no second bank debit |
| GET `/inventory/procurement/suppliers/{id}/summary`, `/ledger` | `stock.view` + `payments.view` | Both domains |
| GET `/inventory/procurement/accounts`, `/accounts/{id}/ledger` | `payments.view` | Workshop cash/bank ledger |
| POST `/inventory/procurement/accounts` | `payments.create` | Ledger account creation |
| GET/POST/PATCH/DELETE `/customers`, `/vehicles`, `/jobs`, `/stock`, `/invoices`, `/staff` | Matching `module.view/create/edit/delete` by HTTP action | `CompanyScopedModelViewSet`, unless explicit `action_permission_map`; see special actions |
| GET `/customers/{id}/payments` | `payments.view` | Cross-module action mapping |
| GET `/customers/{id}/invoices` | `invoices.view` | Cross-module action mapping |
| GET `/customers/{id}/jobs` | `jobs.view` | Cross-module action mapping |
| GET `/customers/{id}/vehicles`, POST `/customers/{id}/vehicles` | `vehicles.view` / `vehicles.create` | Cross-module action mapping |
| GET `/jobs/{id}/cost-summary` | `jobs.view` + `payroll.view` + `expenses.view` + `stock.view` | Multi-domain cost summary |
| POST `/jobs/{id}/issue-part` | `jobs.edit` + `stock.edit` | Stock and job mutation |
| GET `/reports` | `reports.view` | Report export currently shares this read permission |
| GET `/dashboard` | `dashboard.view` | Module-scoped |

## Frontend/role consistency

- `src/config/rolePermissions.js` includes new nested codes. `permissionCodesToMatrix` must preserve all dot-separated suffixes, e.g. `payroll.payments.reverse`.
- `src/context/FeatureAccessContext.jsx` consumes `/saas/credits/access` and does **not** grant permissions. `src/routes/ProtectedRoute.jsx` and sidebar should always use the returned server access intersection. Buttons must have distinct write-code guards.
- `/payroll/daily-wages/overtime-approvals` is an **attendance view** and must not force `payroll.view`; POST decisions require `attendance.overtime.approve`.
- `/payroll/payments` reads confidential wage history and requires `payroll.payments.view`, separate from `payroll.view`.

## Verified outstanding issues / follow-up work

1. **Customer payment consistency (high priority):** `apps/payments/views.py` adjusts `Invoice.paid` only in `perform_create`. Inherited PATCH/DELETE can modify or delete payment records without restoring invoice totals or creating reversal audit. Do not expose payment adjust/reverse capabilities until a transaction-safe immutable reversal model is implemented and tested.
2. **Supplier permission granularity:** Current supplier CRUD is still mapped to `stock.*` by `MODEL_PERMISSION_PREFIX`. A separate supplier domain needs a deliberate paid feature entitlement mapping, migration and frontend rollout.
3. **Attendance manager UI:** Manager screens still need per-button visibility updates for newly granular rights (server rejects unauthorized mutations).
4. **Branch/record scope:** Some generic company-scoped views in customers, payments and expenses do not restrict by staff branch. Do a dedicated branch matrix before labeling multi-branch least privilege complete.
5. **Payroll history data exposure:** The account response suppresses the `payments` list without `payroll.payments.view`, but `audit` and aggregates need a product-approved confidentiality policy.
6. **Approval separation:** A formal two-person policy for salary processing/approval is not enforced across all historical flows (many legacy endpoints are disabled to GET only).
7. **Feature catalog and duplicate route audits:** Company feature activation is company module key-based, not per permission suffix. Review every paid module mapping and unsupported legacy route with integration tests before broad rollout.

## Automated verification

`apps.roles.tests_granular_permissions` tests negative and positive wage/attendance/supplier grants and idempotent migration. Existing attendance self-approval, owner, tenant, supplier-finance, credit/billing regressions are retained in CI. Frontend tests validate nested matrix codes, direct routes, and wage button guards. **Passing CI does not establish production safety:** perform an authenticated staging matrix by owner, read-only wages viewer, rate editor, wage payer, wage reverser, attendance manager, overtime approver, supplier account operator and cross-tenant employee. Test both enabled/disabled modules, inactive roles, inactive tenants and overdue wallet state.
