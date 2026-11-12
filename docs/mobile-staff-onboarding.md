# CubixGear — Manual Staff Onboarding

All staff invitations are manually shared by an authorized workshop administrator.
No Meta WhatsApp Cloud API, webhook, approved message templates, or automated
delivery system is needed.

## Creating staff

1. Workshop admin opens `/staff-management/add` and enters the staff member's
   name, international mobile phone number, branch, role and designation.
2. Django creates employee and login identity once. If login access is enabled,
   the employee initially has an unusable password.
3. Django issues a 24-hour, single-use password setup invitation. The link
   appears only in the authorized initial creation/invitation response; normal
   staff GET and list endpoints never return it.
4. Success screen shows the staff account, pending login setup, and whether a
   setup link is ready. Administrator selects Share on WhatsApp, Copy Setup
   Message or Regenerate Setup Link as needed.
5. WhatsApp opens with a draft. The administrator reviews and presses Send
   themselves. Opening the draft does not prove delivery.
6. Staff opens `/set-password/<token>`, chooses a valid password, and signs in
   with mobile number plus password. A used, expired, superseded or revoked
   link cannot be reused.

## Recovery and security

- `POST /api/v1/employees/<uuid>/invite` with `manualOnly: true` obtains or
  rotates a link and applies existing tenant/role checks and issuance throttling.
- If invitation generation fails during creation, the saved employee is
  preserved; administrators may retry, without creating a duplicate employee.
- If production `FRONTEND_URL` is not HTTPS, regeneration returns an
  explanatory validation error. Production must use a public HTTPS URL.
- Invitation token hashes are stored securely, not the URL itself. Do not put
  the setup URL in logs or persisted browser state.
- Public staff password recovery remains non-enumerating and does not send
  messages. Owners with an email can use the existing email recovery.
- Existing Celery, Redis and billing infrastructure remain in place for
  unrelated application functionality.

## Deployment

Set `FRONTEND_URL=https://your-public-company-panel.example` in the backend
environment, and keep a stable `STAFF_PASSWORD_LINK_SIGNING_KEY`. No
`WHATSAPP_*` or `META_*` credentials are required.

Run `python manage.py check`, `python manage.py makemigrations --check --dry-run`
and `python manage.py test apps.accounts.tests_mobile_staff_onboarding`.
