# PythonAnywhere login: 403 CSRF origin verification

This document covers the error:

    Forbidden (403)
    CSRF verification failed.
    Origin checking failed - null does not match any trusted origins.

**Do not remove CSRF middleware, add \`@csrf_exempt\` to login, or trust
\`null\` / wildcard origins.** A browser sends \`Origin: null\` for an opaque
origin (e.g. a sandboxed iframe, some privacy/security extensions, or a file
URL); it does not identify a trustworthy host. Django rejects it correctly.
Adding the backend URL to \`CSRF_TRUSTED_ORIGINS\` is necessary for legitimate
nonmatching HTTPS origins but **does not make \`null\` valid**.

## 1. Try a normal top-level browser login

- Open **https://cardflow.pythonanywhere.com/login/?next=/** directly in a
  new Chrome/Firefox tab (not from an embedded preview, iframe, \`file://\`
  page, or HTTP version).
- Refresh the form to get a fresh CSRF cookie before submitting again.
- Allow cookies for \`cardflow.pythonanywhere.com\`.
- If the error persists, disable privacy extensions *for this site only*
  or retry in a normal/incognito browser session. Open developer tools,
  Network → login POST → Request Headers, and check \`Origin\`.
- A normal top-level HTTPS form should send
  \`Origin: https://cardflow.pythonanywhere.com\` (or omit \`Origin\` and send
  a valid same-origin \`Referer\`). If the header literally remains \`null\`,
  the browser/request context needs to be corrected; the server must not
  bypass Django's CSRF validation.

## 2. Deploy securely and configure .env

Run in a PythonAnywhere **Bash console**, using your actual deployed repo:

\`\`\`bash
cd /home/cardflow/cubegears-backend
source /home/cardflow/.virtualenvs/cubegears-env/bin/activate
git pull origin main
pip install -r requirements.txt

# Preserves valid existing keys; sets DEBUG=False and the production hostname.
python scripts/setup_pythonanywhere.py --host cardflow.pythonanywhere.com

python manage.py check
python manage.py check --deploy
python manage.py collectstatic --noinput
\`\`\`

This setup program updates the private \`.env\`, but **does not change
environment variables previously exported by the Bash shell, PythonAnywhere
Web settings, or your WSGI file**. Inspect these if effective settings differ.

For a direct HTTPS login, these are the intended settings:

\`\`\`dotenv
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=cardflow.pythonanywhere.com
CSRF_TRUSTED_ORIGINS=https://cardflow.pythonanywhere.com
SECURE_SSL_REDIRECT=True
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
\`\`\`

Keep your other required allowed hosts, CORS origins, and CSRF origins when
updating. Do **not** copy these example lines over the generated secret keys
or SMTP values, and do not print/paste the secret values.

To inspect *effective settings without printing secrets*:

\`\`\`bash
python manage.py shell -c "from django.conf import settings; print('DEBUG:', settings.DEBUG); print('ALLOWED_HOSTS:', settings.ALLOWED_HOSTS); print('CSRF_TRUSTED_ORIGINS:', settings.CSRF_TRUSTED_ORIGINS); print('SECURE_REFERRER_POLICY:', settings.SECURE_REFERRER_POLICY); print('CSRF_COOKIE_SECURE:', settings.CSRF_COOKIE_SECURE); print('SESSION_COOKIE_SECURE:', settings.SESSION_COOKIE_SECURE)"
\`\`\`

Expected: \`DEBUG: False\`, backend host in \`ALLOWED_HOSTS\`, and backend
HTTPS origin in \`CSRF_TRUSTED_ORIGINS\`. The global referrer policy is
\`origin\`, so HTTPS login POSTs can pass Django's standard Referer fallback
if the browser does not send an Origin header, without revealing sensitive
password-reset and magic-link query strings in Referer.

If you still see the full Django debug help page, the live WSGI application
is running with \`DEBUG=True\`. Check for \`DJANGO_DEBUG\` exported by the
PythonAnywhere WSGI file or Web environment (overrides \`.env\`), then reload
the web app. Changing Bash shell settings does not guarantee that the WSGI
process shares those settings.

## 3. PythonAnywhere Web configuration

- PythonAnywhere → **Web** → \`cardflow.pythonanywhere.com\`.
- Virtualenv: \`/home/cardflow/.virtualenvs/cubegears-env\`.
- WSGI file: \`/var/www/cardflow_pythonanywhere_com_wsgi.py\`.
- Ensure the WSGI file imports your actual \`config.wsgi.application\`, and
  does not override \`DJANGO_DEBUG\` or the secret key with older values.
- Configure \`/static/\` → \`/home/cardflow/cubegears-backend/staticfiles\`.
- Click **Reload** after changes. Retest in a new top-level HTTPS tab.

For Django 6.0 CSRF behavior, see
https://docs.djangoproject.com/en/6.0/ref/csrf/ .

## Security regression tests

\`\`\`bash
python manage.py test apps.control_panel.test_csrf_origin --noinput
\`\`\`

The tests assert that normal same-origin HTTPS login works, a valid
same-origin Referer is accepted when Origin is absent, and an explicit
\`Origin: null\`, a foreign origin, or a missing secure Referer are rejected
with 403. CSRF protection remains enabled.
