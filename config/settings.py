import hashlib
import os
from datetime import timedelta
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

def env(name, default=None):
    return os.getenv(name, default)

def env_bool(name, default=False):
    return str(env(name, str(default))).lower() in {"1", "true", "yes", "on"}

def env_list(name, default=""):
    return [item.strip() for item in str(env(name, default)).split(",") if item.strip()]

SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-only-change-me")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost")

# Keep strong security settings for production; SQLite is supported on
# a server with persistent local storage, never an ephemeral Vercel function.
if not DEBUG:
    if not env("DJANGO_SECRET_KEY") or len(SECRET_KEY.encode("utf-8")) < 32:
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY must be a strong value (32+ bytes) in production. "
            "On PythonAnywhere, run 'python scripts/setup_pythonanywhere.py "
            "--host YOUR_HOST' and check the private .env and exported environment variables."
        )
    if not env("DJANGO_ALLOWED_HOSTS"):
        raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS must list your production backend hosts.")
    if env_bool("VERCEL", False):
        raise ImproperlyConfigured(
            "SQLite needs persistent writable storage. Vercel Functions cannot persist db.sqlite3; "
            "host this Django backend on a VPS or service with a persistent disk."
        )

JWT_SIGNING_KEY = env("JWT_SIGNING_KEY", SECRET_KEY)
if len(JWT_SIGNING_KEY.encode("utf-8")) < 32:
    if DEBUG:
        # Local-development fallback only. Production must provide a real
        # high-entropy key of at least 32 bytes.
        JWT_SIGNING_KEY = hashlib.sha256(
            JWT_SIGNING_KEY.encode("utf-8")
        ).hexdigest()
    else:
        raise ImproperlyConfigured(
            "JWT_SIGNING_KEY must be at least 32 bytes long in production."
        )

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "apps.companies",
    "apps.branches",
    "apps.roles",
    "apps.accounts",
    "apps.customers",
    "apps.vehicles",
    "apps.services",
    "apps.jobs",
    "apps.inventory",
    "apps.invoices",
    "apps.payments",
    "apps.expenses",
    "apps.employees",
    "apps.attendance",
    "apps.payroll",
    "apps.notifications",
    "apps.reports",
    "apps.saas",
    "apps.credits",
    "apps.control_panel",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",

    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {
        "context_processors": [
            "django.template.context_processors.request",
            "django.contrib.auth.context_processors.auth",
            "django.contrib.messages.context_processors.messages",
        ],
    },
}]

WSGI_APPLICATION = "config.wsgi.application"

# SQLite-only: no Prisma/PostgreSQL connection or DATABASE_URL is used.
# For a persistent deployment, mount a writable volume and point this variable
# to an absolute filename, e.g. /var/lib/cubixgear/db.sqlite3.
SQLITE_DATABASE_PATH = Path(
    env("SQLITE_DATABASE_PATH", str(BASE_DIR / "db.sqlite3"))
).expanduser()
if not SQLITE_DATABASE_PATH.is_absolute():
    SQLITE_DATABASE_PATH = BASE_DIR / SQLITE_DATABASE_PATH

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": SQLITE_DATABASE_PATH,
        "OPTIONS": {"timeout": 30},
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = env("DJANGO_TIME_ZONE", "Asia/Kolkata")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
MEDIA_URL = "/media/"
# Public company logos must live on persistent storage in production.
MEDIA_ROOT = Path(env("DJANGO_MEDIA_ROOT", str(BASE_DIR / "media"))).expanduser()
if not MEDIA_ROOT.is_absolute():
    MEDIA_ROOT = BASE_DIR / MEDIA_ROOT
# Outside MEDIA_ROOT: public /media/ routes must never expose private job photos.
PRIVATE_JOB_MEDIA_ROOT = Path(env("PRIVATE_JOB_MEDIA_ROOT", str(BASE_DIR / "private_job_media")))

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "accounts.User"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    "DEFAULT_RENDERER_CLASSES": (
        "rest_framework.renderers.JSONRenderer",
    ),
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=int(env("JWT_ACCESS_MINUTES", 30))),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=int(env("JWT_REFRESH_DAYS", 7))),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "SIGNING_KEY": JWT_SIGNING_KEY,
    # Existing JWTs are invalidated when the user's password hash changes.
    "CHECK_REVOKE_TOKEN": True,
}

CORS_ALLOWED_ORIGINS = env_list(
    "CORS_ALLOWED_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000,http://127.0.0.1:3000",
)
CORS_ALLOW_CREDENTIALS = env_bool("CORS_ALLOW_CREDENTIALS", False)

# Use console mail locally, direct Django SMTP by default in production.
# Override EMAIL_BACKEND in .env when a different provider is required.
EMAIL_BACKEND = env(
    "EMAIL_BACKEND",
    "django.core.mail.backends.console.EmailBackend" if DEBUG
    else "django.core.mail.backends.smtp.EmailBackend",
)
EMAIL_HOST = env("EMAIL_HOST", "")
EMAIL_PORT = int(env("EMAIL_PORT", 587))
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
EMAIL_TIMEOUT = int(env("EMAIL_TIMEOUT", 15))
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL", False)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "CubixGear <no-reply@cubixgear.com>")

FRONTEND_URL = env("FRONTEND_URL", "http://localhost:5173")
MAGIC_LINK_EXPIRY_MINUTES = int(env("MAGIC_LINK_EXPIRY_MINUTES", 15))
PASSWORD_RESET_TIMEOUT = int(env("PASSWORD_RESET_TIMEOUT_SECONDS", 3600))

# Django's CsrfViewMiddleware validates Referer on HTTPS POST requests when
# Origin is absent.  A global "no-referrer" policy suppresses this fallback
# and breaks legitimate same-origin form submissions in some browsers.
# "origin" sends only scheme + host + port, never sensitive reset/magic-link
# paths or tokens, while preserving Django's secure CSRF verification.
# Explicit Origin: null is still rejected by Django (do not whitelist it).
SECURE_REFERRER_POLICY = "origin"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"


CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS", "")
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", not DEBUG)
SECURE_HSTS_SECONDS = int(env("SECURE_HSTS_SECONDS", 3600 if not DEBUG else 0))
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE", not DEBUG)
CSRF_COOKIE_SECURE = env_bool("CSRF_COOKIE_SECURE", not DEBUG)

LOGIN_URL = "panel:login"
LOGIN_REDIRECT_URL = "panel:dashboard"
LOGOUT_REDIRECT_URL = "panel:login"


# Celery / Redis configuration is disabled for PythonAnywhere WSGI.
# Do not remove it: these settings can be restored when a worker + Redis
# broker are available. The attendance auto-close beat task will NOT run
# automatically while Celery is disabled.
# CELERY_BROKER_URL = env("CELERY_BROKER_URL", "redis://127.0.0.1:6379/0")
# CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", "redis://127.0.0.1:6379/1")
# CELERY_ACCEPT_CONTENT = ["json"]
# CELERY_TASK_SERIALIZER = "json"
# CELERY_RESULT_SERIALIZER = "json"
# CELERY_TIMEZONE = TIME_ZONE
# CELERY_TASK_TRACK_STARTED = True
# CELERY_TASK_TIME_LIMIT = int(env("CELERY_TASK_TIME_LIMIT", 120))
# CELERY_TASK_SOFT_TIME_LIMIT = int(env("CELERY_TASK_SOFT_TIME_LIMIT", 90))
# CELERY_TASK_ALWAYS_EAGER = env_bool("CELERY_TASK_ALWAYS_EAGER", False)
# CELERY_TASK_EAGER_PROPAGATES = env_bool("CELERY_TASK_EAGER_PROPAGATES", True)


# CELERY_BEAT_SCHEDULE = {
#     "auto-close-attendance-sessions": {
#         "task": "apps.attendance.tasks.auto_close_attendance_sessions",
#         "schedule": 300.0,
#     },
# }

# Razorpay SaaS subscription checkout. Configure TEST keys before enabling.
RAZORPAY_KEY_ID = env("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = env("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET = env("RAZORPAY_WEBHOOK_SECRET", "")
CREDIT_FEATURE_ENFORCEMENT_ENABLED = env_bool("CREDIT_FEATURE_ENFORCEMENT_ENABLED", False)
# When enabled, paid module keys not yet in PlanFeature fail closed rather than
# silently exposing unpriced operations. Roll out only after catalog reconciliation.
CREDIT_FEATURE_STRICT_GATE_ENABLED = env_bool("CREDIT_FEATURE_STRICT_GATE_ENABLED", False)
# Old subscription paywall is retired. Legacy transactions remain for audit history.
