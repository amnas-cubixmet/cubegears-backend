# CubeGears — Mobile-only workshop and staff onboarding

This work builds on the customer tracking / official Meta WhatsApp integration
feature branch. **Deploy the foundation before enabling this flow.** No existing
users, tenant roles or invoices are deleted or migrated to a different account.

## Expected workflow

1. Public workshop owner submits name, workshop name, **E.164 mobile number**
   (e.g. `+919876543210`), and optionally email. Consent to official WhatsApp
   delivery is required when no email is given.
2. Backend creates the existing Company, head Branch, system Super Admin role,
   User with an **unusable password**, and Employee profile. A one-use 24-hour
   password invitation is generated. **The public signup response never returns
   the raw setup URL.**
3. If email was provided, the existing Celery email system handles the link.
   Otherwise, only an independently configured **platform** Meta WhatsApp
   Business sender can deliver the owner onboarding link. A just-created
   workshop cannot send from its own unconnected WABA. Without a platform sender,
   the owner account remains pending and requires operator intervention. Do
   not enable email-free public signup in production until the sender works.
4. Authorized company admin creates staff with full name, international mobile,
   optional email, branch and an assignable, non-system company role. If
   `loginAccessEnabled` is true, a user with no usable password is created.
5. An invitation is issued immediately; the old unused invitation is
   invalidated. Staff must **explicitly consent** and the company must have
   an official connected WABA, approved `staff_onboarding` template, enabled
   event, active Celery and `WHATSAPP_SEND_ENABLED` for automation.
6. The signed admin-only staff invitation response includes an ephemeral
   `setupLink` for manual trusted sharing when automated sending is disabled
   or unavailable. Only administrators with `company.manage` may issue it.
   **Do not log or store this raw link in an audit feed.**
7. Staff enters and confirms their password at `/set-password/:token`. Django
   validates it using AUTH_PASSWORD_VALIDATORS, hashes it and atomically consumes
   the one-use token.
8. Staff logs in to existing JWT auth with `identifier: +919876543210` and
   `password`. Email+password still works. When a mobile number exists in
   more than one workshop, the API requires an explicit `workshop` slug after
   successful password verification. Existing role and tenant restrictions
   continue to apply.

## API surface

- `POST /api/v1/auth/signup`: `workshop_name`, `owner_name`, `mobile`,
  optional `email`, `whatsapp_consent`.
- `POST /api/v1/auth/login`: `identifier` (email or E.164 mobile),
  `password`, optional `workshop` slug. Legacy `email` credential accepted.
- `POST /api/v1/auth/staff-invitation/validate`: `token` (generic
  expired/revoked invalid response).
- `POST /api/v1/auth/staff-invitation/consume`: `token`, `password`,
  `confirmPassword`. No session issued automatically.
- `POST /api/v1/employees`: existing staff fields plus
  `loginAccessEnabled`, `whatsappConsent`, `whatsappConsentSource`.
- `POST /api/v1/employees/<employee-uuid>/invite`: invalidate old token,
  create fresh link, schedule Meta send or return manual copy/share link.
- `PATCH /api/v1/employees/<employee-uuid>/login-access`: `enabled`
  boolean; changes login only, not payroll or employment status.
- `GET /api/v1/whatsapp/settings`: per-company official Meta connection and
  template settings; `staff_onboarding` is a supported event.
- `POST /api/v1/whatsapp/templates`: must point to an already Meta-approved
  template; `staff_onboarding` uses exactly three ordered body parameters:
  staff name, workshop name, one-time setup URL.
- `POST /api/v1/whatsapp/webhook`: requires Meta
  `X-Hub-Signature-256` HMAC and updates invite delivery status.

No OTP authentication endpoints or fabricated JWT sessions.

## Required deployment configuration

Set secrets only in the server environment:

- `SECRET_KEY`, `JWT_SIGNING_KEY` — existing secure Django/JWT secrets.
- `STAFF_PASSWORD_LINK_SIGNING_KEY` — stable high-entropy dedicated HMAC key
  (changing it invalidates unconsumed links).
- `FRONTEND_URL` — HTTPS company-panel base URL.
- `CORS_ALLOWED_ORIGINS` — exact production company-panel origins.
- `WHATSAPP_META_APP_ID`, `WHATSAPP_META_APP_SECRET`,
  `WHATSAPP_META_CONFIG_ID` — official Meta app/signup integration.
- `WHATSAPP_TOKEN_ENCRYPTION_KEY` — valid Fernet key for company tokens.
- `WHATSAPP_WEBHOOK_VERIFY_TOKEN` — random Meta webhook verification token.
- `WHATSAPP_SEND_ENABLED=false` until template, consent and QA checks pass.
- `CELERY_BROKER_URL` — reachable Redis-backed Celery broker and worker.
- `WHATSAPP_PLATFORM_PHONE_ID`, `WHATSAPP_PLATFORM_ACCESS_TOKEN`,
  `WHATSAPP_PLATFORM_TEMPLATE`, `WHATSAPP_PLATFORM_TEMPLATE_LANGUAGE`
  — **separate verified platform sender**, needed only for email-free new
  workshop owner invitations (never use an unconnected new company's WABA).

For development **keep `WHATSAPP_SEND_ENABLED=false`**. No live Meta message
should be sent during tests. Generate a new encryption key with Fernet in a
secure operator environment and do not paste it into source control.

### Deploy

```sh
python manage.py makemigrations --check --dry-run
python manage.py migrate --noinput
python manage.py check
python manage.py test apps.accounts.tests_mobile_staff_onboarding --noinput
```

Frontend should be built with a correctly configured `VITE_API_URL`. HTTPS
and no-referrer headers are required for password-link safety.

## Compatibility and outstanding production gates

- Existing legacy accounts with local phone formatting are **not guessed** as
  `+91` or any other country. Convert to verified international format through
  authorized account migration/admin review before enabling phone login.
- Duplicate historical phone numbers must be reviewed, not silently assigned
  to one workshop account.
- Email/password login and older setup/reset URLs remain supported.
- Public signup is **not verified proof of phone ownership**; successful link
  delivery through Meta is necessary before activation.
- Verify persistent Celery workers, real Meta template approval, signed webhooks,
  phone opt-in source, idempotency, rate limits and mobile browser UX in staging.
- No private setup token, password or Meta bearer secret should be stored in logs.
