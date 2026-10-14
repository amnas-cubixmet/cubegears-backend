# PythonAnywhere deployment (without Celery)

The Django WSGI app starts without importing Celery. Password setup, reset,
and magic-link emails are delivered by Django's SMTP backend directly (no
Celery/Redis worker). **Attendance auto-checkout scheduled by Celery Beat will
not run automatically** until a scheduler is installed and configured.

## First deployment

Open a PythonAnywhere Bash console:

```bash
cd /home/cardflow/cubegears-backend
source /home/cardflow/.virtualenvs/cubegears-env/bin/activate
git pull origin main
pip install -r requirements.txt
```

## Fix DJANGO_SECRET_KEY safely (recommended)

Run the production setup helper from the project directory. It creates
`.env` with restricted permissions, generates high-entropy secret keys when
needed, preserves any existing valid keys (to avoid breaking sessions), and
sets `DJANGO_DEBUG=False`, the backend hostname and CSRF origins. It does
**not** print secret values or require Celery:

```bash
python scripts/setup_pythonanywhere.py --host cardflow.pythonanywhere.com --frontend-url https://cubegears-company-panel.vercel.app
```

Replace the frontend URL with your **actual** deployed frontend HTTPS origin.
Running this script more than once is safe: existing valid keys stay the same.
If an old shell/WSGI environment variable overrides the secret in `.env`,
the script warns you. Fix or unset the old exported value before running
management commands, and ensure the PythonAnywhere WSGI process uses the same
keys. Do **not** disable the production security validation to bypass errors.

The following is an example of the values stored in the private
`/home/cardflow/cubegears-backend/.env`; **do not overwrite** generated keys.
The `.env` file is gitignored and must never be committed or pasted in chat.

```dotenv
DJANGO_DEBUG=False
DJANGO_SECRET_KEY=<PASTE_YOUR_GENERATED_SECRET_KEY>
JWT_SIGNING_KEY=<PASTE_ANOTHER_RANDOM_SECRET_KEY>
DJANGO_ALLOWED_HOSTS=cardflow.pythonanywhere.com
DJANGO_TIME_ZONE=Asia/Kolkata
FRONTEND_URL=https://your-frontend-domain.example
CORS_ALLOWED_ORIGINS=https://your-frontend-domain.example
CSRF_TRUSTED_ORIGINS=https://cardflow.pythonanywhere.com

# Django sends email through SMTP (not Celery):
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.your-provider.example
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=your-smtp-username
EMAIL_HOST_PASSWORD=your-smtp-password
DEFAULT_FROM_EMAIL=CubixGear <verified-sender@your-domain.example>
```

Choose an SMTP provider that is accessible from your PythonAnywhere plan and
supports the configured sender. Do not set the sample host or credentials
literally. If your provider requires port 465 (implicit SSL), use `EMAIL_PORT=465`,
`EMAIL_USE_TLS=False`, and `EMAIL_USE_SSL=True`. Never enable both TLS and
SSL together.

The WSGI file, `/var/www/cardflow_pythonanywhere_com_wsgi.py`, should
activate the same virtualenv configured in the PythonAnywhere **Web** tab,
make your backend importable, then load Django:

```python
import os
import sys

project_path = "/home/cardflow/cubegears-backend"
if project_path not in sys.path:
    sys.path.insert(0, project_path)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()
```

The project already loads `BASE_DIR/.env` in `config/settings.py`. Do not
hardcode the secret key in your WSGI file or your repository.

Once configured, verify the real production environment (if you previously
exported an old `DJANGO_SECRET_KEY`, remove that override first):

```bash
python manage.py check
python manage.py check --deploy
python manage.py migrate --noinput
python manage.py collectstatic --noinput
```

In PythonAnywhere **Web**, set the virtualenv to
`/home/cardflow/.virtualenvs/cubegears-env`, configure static file mapping
`/static/` to `/home/cardflow/cubegears-backend/staticfiles`, and click
**Reload**. Inspect the **error log** if WSGI still fails.

Verify outbound SMTP with a **mailbox you control**:

```bash
python manage.py shell -c "from django.core.mail import send_mail; from django.conf import settings; send_mail('CubixGear SMTP test', 'Django email without Celery', settings.DEFAULT_FROM_EMAIL, ['you@your-domain.example'], fail_silently=False)"
```

Replace `you@your-domain.example` with your own email address; the return
value `1` indicates the message was accepted by the SMTP backend, not
guaranteed inbox delivery. Check the inbox/spam folder.

If `EMAIL_BACKEND` is missing in production, Django now defaults to SMTP;
configure real `EMAIL_HOST`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`,
and a verified sender before testing email endpoints.

## Re-enable Celery later

When using a supported Redis broker and Celery worker, uncomment:

- `celery[redis]` in `requirements.txt`
- the import/export in `config/__init__.py`
- the Celery settings in `config/settings.py`

The original `config/celery.py`, `apps/accounts/tasks.py`, and
`apps/attendance/tasks.py` remain in the repository. Reconfigure the
application to dispatch tasks only after workers, queues and scheduling are
operational. Email requests currently use direct Django delivery by design.
