# CubixGear authentication rollout (Company + SaaS Control Panel)

## Login surfaces and privilege boundary

| Surface | URL | Authentication |
|---|---|---|
| Company Panel (React) | `https://app.example.com/login` | Email/password; one-click magic link; invite password setup; forgot/reset |
| SaaS Control Panel (Django) | `https://api.example.com/login/` | Email/password; one-click admin magic link; invite password setup; forgot/reset |
| Platform Django admin | `https://api.example.com/admin/` | Django superuser authentication, separate privilege rules |

**A company SUPER_ADMIN is not a platform superuser.** Their credentials must not create or grant platform panel access.

A platform user created from Users & Access without a password gets a setup email automatically, if they have authorized panel permissions. A company user with company and role receives a Company Panel setup URL. Never email actual passwords.

All setup/reset links use Django's signed, password-bound token and become invalid after changing the password. Magic links are hashed, single-use, expire via `MAGIC_LINK_EXPIRY_MINUTES`, and are revoked on password change. Public recovery responses are generic for unknown addresses; password login reports invalid credentials.

## Production environment variables

Place secrets in the **server's private environment**, not Git or the frontend build. Start from `.env.example` and replace all placeholders.

```dotenv
DJANGO_DEBUG=False
DJANGO_SECRET_KEY=REPLACE_WITH_INDEPENDENT_RANDOM_32_PLUS_BYTES
JWT_SIGNING_KEY=REPLACE_WITH_ANOTHER_RANDOM_SECRET
DJANGO_ALLOWED_HOSTS=api.example.com
FRONTEND_URL=https://app.example.com
CORS_ALLOWED_ORIGINS=https://app.example.com
CSRF_TRUSTED_ORIGINS=https://api.example.com
SQLITE_DATABASE_PATH=/var/lib/cubixgear/db.sqlite3
SECURE_SSL_REDIRECT=True
SECURE_HSTS_SECONDS=31536000
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True

EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.example.com
EMAIL_PORT=587
EMAIL_HOST_USER=REPLACE_WITH_SMTP_LOGIN
EMAIL_HOST_PASSWORD=REPLACE_WITH_PRIVATE_SMTP_PASSWORD
EMAIL_USE_TLS=True
EMAIL_USE_SSL=False
EMAIL_TIMEOUT=15
DEFAULT_FROM_EMAIL=CubixGear <no-reply@example.com>

PASSWORD_RESET_TIMEOUT_SECONDS=3600
MAGIC_LINK_EXPIRY_MINUTES=15
JWT_ACCESS_MINUTES=30
JWT_REFRESH_DAYS=7
```

Frontend Vercel environment:

```dotenv
VITE_API_URL=https://api.example.com/api/v1
VITE_USE_MOCK_API=false
VITE_API_TIMEOUT=15000
VITE_API_WITH_CREDENTIALS=false
```

**Important:** The SaaS Control Panel session uses same-origin secure cookies, while React uses JWT bearer credentials. Never put `DJANGO_SECRET_KEY`, SMTP credentials or API signing keys into `VITE_*` variables.

## Database and email operational requirements

- Current selected production architecture is **SQLite on persistent storage**, behind **one Django application instance**, with regular encrypted SQLite Online Backups and verified restores. Avoid ephemeral/function hosts for writable SQLite.
- Configure a verified SMTP sender with SPF/DKIM/DMARC and confirm outbound SMTP connectivity. No real email will arrive when EMAIL_BACKEND uses console.
- Email is synchronous via an on-commit callback, so an SMTP outage is logged rather than changing the result of public recovery requests. Configure error monitoring and delivery alerts.
- Control Panel has an application-level cache throttle. With more than one web worker, a **shared cache backend** is required for consistent throttling. Local memory per worker is not adequate for a high-scale public deployment.
- Use HTTPS and a properly trusted proxy; confirm Django sees the original HTTPS scheme before issuing admin email links.
- `CHECK_REVOKE_TOKEN=True` means pre-deployment JWTs without a password hash claim need a fresh login once after rollout. This is an intentional security change.

## Staging QA before production

1. Create a test workshop account; check setup email, company-only permissions and its password login.
2. Create a test platform admin without password; check it receives **SaaS Control Panel** password setup URL and can open its dashboard but not unauthorized modules.
3. Request magic links for both surfaces; click once to sign in, confirm second use fails, test expired links.
4. Request reset with an unknown email; response must not expose membership or send an email.
5. Reset a real account, verify old password/session and previously issued magic links no longer work.
6. Confirm all responsive login/setup/reset pages, branded HTML and plain-text email fallback.
7. Run `python manage.py check --deploy`, `python manage.py makemigrations --check --dry-run`, `python manage.py migrate`, `python manage.py collectstatic`; restart Django; back up database first.
8. Inspect production logs and email delivery without sharing signed links or secret values.

Deployment is not complete merely because CI passes; live HTTPS, mail delivery, backups and browser flows require staging smoke testing.


## Staff onboarding and password-reset email troubleshooting

In local development the default `EMAIL_BACKEND` is
`django.core.mail.backends.console.EmailBackend`. This prints the signed
setup/reset email to the Django terminal. **It does not send to Gmail or any
other inbox.** Staff creation can succeed while the account remains in
`Password Setup Pending` until the person follows a valid setup link.

For real emails, set the following values in your **private backend `.env`**
(not React/Vite `.env`). Use your own verified sender and SMTP app password:

```dotenv
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_HOST_USER=your-smtp-account@example.com
EMAIL_HOST_PASSWORD=YOUR_PRIVATE_SMTP_APP_PASSWORD
EMAIL_USE_TLS=True
EMAIL_USE_SSL=False
DEFAULT_FROM_EMAIL=CubixGear <your-smtp-account@example.com>
FRONTEND_URL=http://localhost:5173
```

Then restart Django and run these diagnostic commands from the backend directory:

```bash
python manage.py check_email_delivery
python manage.py check_email_delivery --to your-test-recipient@example.com
```

The second command sends only a non-sensitive test message. A successful
result means SMTP accepted it, not that it reached the inbox. Check spam and
provider reputation/SPF/DKIM for production. Do not paste app passwords,
SMTP secrets, tokens or password setup URLs into issues or chat.

When creating staff, select a valid company-specific login role, enter a
valid email and keep the staff account active. The Staff Profile now shows
the create/invite email state. If email delivery failed, **do not create
the staff again**: fix SMTP and choose **Send Login Invite** on the existing
Staff Profile. Only company admins are authorized to send login invites.

Password-reset and magic-link request endpoints always return the same
public response for registered and unknown emails. This is intentional
protection against identifying which accounts exist. A generic confirmation
does not prove that email was delivered. For account access problems,
verify the staff's email and active account/role privately as an authorized
workshop admin.
