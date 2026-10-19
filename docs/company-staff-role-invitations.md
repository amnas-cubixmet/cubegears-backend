# Company-created roles and staff email access

Staff forms select only active, non-system roles created in the authenticated company. The assignable list omits owner/platform/wildcard/billing roles and roles the actor cannot delegate. Global and other-company roles never appear. The main Roles list omits system roles; owner authority remains protected internally rather than an assignable staff role. Role creation/update remains owner-only. Role names must be unique within a company for new changes; pre-existing duplicate names are disambiguated by roleId when creating/editing a login account.

Employee payloads cannot bind arbitrary users, change tenant/company, assign platform flags or provide custom permission overrides. The selected role ID must belong to the authenticated company and match any submitted role name. No per-staff override section is added. Staff receive only their role's permissions intersected with company feature and billing access. Login, auth/me and password setup never rewrite roles or seed additional staff roles.

Creating an active staff profile with email creates the login under that email and company with an unusable initial password. After the transaction commits, the invitation is published to Celery. Its secure email link creates the password, verifies email ownership and returns the existing tenant/role-scoped session. Invitations include the company, assigned role, login email and login page. Existing activated staff get the existing one-use magic-link flow when re-invited; their password is preserved.

## Worker setup

Configure the same private environment for Django and Celery:

```
EMAIL_CELERY_ENABLED=True
CELERY_BROKER_URL=redis://127.0.0.1:6379/0
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
FRONTEND_URL=http://localhost:5173
```

Also configure DEFAULT_FROM_EMAIL, EMAIL_HOST, EMAIL_PORT, EMAIL_HOST_USER, EMAIL_HOST_PASSWORD and exactly one appropriate EMAIL_USE_TLS/EMAIL_USE_SSL setting privately. Use the provider's credentials/app password. Run Redis and a Celery worker; the web server does not send SMTP itself. For local Windows development, the existing worker can run with `celery -A config worker --pool=solo -l info` in a separate terminal. Restart web/worker after changing their environment.

`python manage.py check_email_delivery` diagnoses readiness without sending anything. The optional `--to` diagnostic queues a non-sensitive test via Celery rather than bypassing the worker. No real email was sent by implementation tests; the worker is simulated with the local test email backend. Production SMTP/inbox delivery still needs verification in the user's environment.

Delivery states are precise: pending means transaction commit is awaited, queued means the broker accepted the message, disabled means the Redis/Celery email path is not enabled, and failed means dispatch failed. Queued does not prove inbox receipt. Worker SMTP failures use the existing Celery retries. If Redis/broker dispatch fails, save is retained and the owner must use Send Login Invite to retry rather than create another staff record. A durable mail outbox is not part of this change.

No database migration is required. Existing protected owner and custom role records are retained. The unused legacy StaffFormSheet with hard-coded roles and an unconditional salary permission bypass is removed; current staff add/edit flows use the real company role endpoint.
