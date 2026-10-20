# Invoice & Estimate Template Parity

This change connects company-scoped visual document configuration to three places:
the SaaS Account template editor, browser invoice/estimate preview, and authorized
server-generated A4 PDF.

## Storage and permissions

- `GET/PUT /api/v1/settings/document_templates`: existing company-specific
  `CompanySetting` row. Existing `settings.view` / `settings.manage` RBAC applies.
- Only supported template keys, variants, and booleans are accepted. Uploaded logo
  is inline PNG/JPG/WEBP (<=250 KiB, <=2048px per edge), not SVG or remote URL.
- Legal seller name/GST identity and amounts **continue to come from the saved
  invoice and company registration**, not from arbitrary visual template inputs.
- Selecting invoice and estimate templates is independent. Custom footer, optional
  bank details/signature, accent and compact layout are presentation options.
- Browser localStorage is no longer the source of truth for production settings.

## Finalization invariant

`Invoice.template_snapshot` is a JSON document set atomically on
`POST /billing/documents/<id>/finalize`. It is returned read-only through the
invoice serializer and **cannot** be changed by editing a later draft or updating
the company template. Estimate-to-invoice conversion creates a new draft with no
inherited estimate appearance. Records already finalized before migration 0004
use their original legacy blue PDF presentation instead of silently adopting a
new company template.

The browser uses the current saved company template for new drafts, and the
stored invoice snapshot for finalized documents. The PDF uses the same selection
and appearance configuration through `effective_template_for_invoice`.

The PDF is created from saved invoice data only; unsaved browser edits are not
included. Stock debit, GST checks, tax figures, payments and existing invoice
permissions are unchanged.

## Deployment

Deploy backend changes first, run `python manage.py migrate`, and ensure
`python manage.py check` passes. Then deploy the matching company panel.
Backend/frontend branches: `feat/invoice-template-parity-20261020`.

Suggested backend focused tests:
```powershell
python manage.py test apps.invoices.tests_template_appearance
```

Frontend: `npm test`, `npm run build`, `npm run test:ui`.

Manually verify the account template editor as two distinct companies, then
create a draft, preview, finalize and download its PDF. After finalizing,
switch the company template and confirm the previously issued invoice does not
change. Also check regular and GST invoices, estimates, real logo PNG/JPG/WEBP,
mobile layout, bank details, and the empty logo case.

PDF and browser preview share document styles and sections, but ReportLab uses
native print typography and pagination, so pixel-for-pixel matching to a
responsive browser is not guaranteed.
