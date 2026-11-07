import hashlib
import json
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

# Verified statutory zero-tax HSN/SAC eligibility is deliberately not guessed.
# Authorized tax administrators populate this server-side map after confirming
# classification. Empty mapping blocks all zero GST for registered workshops.
try:
    ESTIMATE_ZERO_GST_CLASSIFICATIONS = json.loads(
        env("ESTIMATE_ZERO_GST_CLASSIFICATIONS", "{}")
    )
except (ValueError, TypeError) as exc:
    raise ImproperlyConfigured("ESTIMATE_ZERO_GST_CLASSIFICATIONS must be valid JSON.") from exc
if not isinstance(ESTIMATE_ZERO_GST_CLASSIFICATIONS, dict) or any(
    not isinstance(code, str)
    or not isinstance(treatments, list)
    or not all(t in {"nil_rated", "exempt", "non_gst", "out_of_scope"}
               for t in treatments)
    for code, treatments in ESTIMATE_ZERO_GST_CLASSIFICATIONS.items()
):
    raise ImproperlyConfigured("ESTIMATE_ZERO_GST_CLASSIFICATIONS must map HSN/SAC codes to permitted treatment arrays.")

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
    "apps.suppliers",
    "apps.invoices",
    "apps.payments",
    "apps.expenses",
    "apps.employees",
    "apps.attendance",
    "apps.payroll",
    "apps.notifications",
    "apps.whatsapp",
    "apps.reports",
    "apps.gstr",
    "apps.saas",
    "apps.credits",
    "apps.control_panel",
    "apps.finance",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "common.request_ids.RequestIdMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.credits.payment_gate.MonthlyPaymentGateMiddleware",
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

# Production PostgreSQL (never silently switch the existing SQLite file).
# Supply either DATABASE_URL OR POSTGRES_HOST / DB / USER / PASSWORD.
# Local development remains SQLite unless PostgreSQL is explicitly selected.
# Production requires PostgreSQL by default; an existing legacy SQLite host can
# temporarily opt in with ALLOW_LEGACY_PRODUCTION_SQLITE=True during migration.
DATABASE_URL = (env("DATABASE_URL") or "").strip()
POSTGRES_HOST = (env("POSTGRES_HOST") or "").strip()
POSTGRES_SSLMODE_RAW = (env("POSTGRES_SSLMODE") or "").strip()
POSTGRES_SSLMODE = POSTGRES_SSLMODE_RAW or ("require" if not DEBUG else "prefer")
POSTGRES_CONNECT_TIMEOUT_SECONDS = int(env("POSTGRES_CONNECT_TIMEOUT_SECONDS", 10))
if not 1 <= POSTGRES_CONNECT_TIMEOUT_SECONDS <= 120:
    raise ImproperlyConfigured("POSTGRES_CONNECT_TIMEOUT_SECONDS must be 1–120.")
DB_CONN_MAX_AGE = int(env("DB_CONN_MAX_AGE", 60))
if DB_CONN_MAX_AGE < 0:
    raise ImproperlyConfigured("DB_CONN_MAX_AGE must be 0 or a positive integer.")

if DATABASE_URL and POSTGRES_HOST:
    raise ImproperlyConfigured(
        "Configure only DATABASE_URL or POSTGRES_HOST/POSTGRES_* fields, not both."
    )

if DATABASE_URL or POSTGRES_HOST:
    if POSTGRES_SSLMODE not in {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}:
        raise ImproperlyConfigured("Invalid POSTGRES_SSLMODE.")
    if DATABASE_URL:
        import dj_database_url
        database = dj_database_url.parse(
            DATABASE_URL, conn_max_age=DB_CONN_MAX_AGE, conn_health_checks=True,
        )
        if database["ENGINE"] != "django.db.backends.postgresql":
            raise ImproperlyConfigured("DATABASE_URL must use PostgreSQL (postgresql://).")
    else:
        required = ["POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"]
        missing = [name for name in required if not (env(name) or "").strip()]
        if missing:
            raise ImproperlyConfigured(
                "PostgreSQL connection is missing: " + ", ".join(missing)
            )
        try:
            port = int(env("POSTGRES_PORT", "5432"))
            if not 1 <= port <= 65535:
                raise ValueError()
        except (TypeError, ValueError) as exc:
            raise ImproperlyConfigured("POSTGRES_PORT must be a valid TCP port.") from exc
        database = {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("POSTGRES_DB"),
            "USER": env("POSTGRES_USER"),
            "PASSWORD": env("POSTGRES_PASSWORD"),
            "HOST": POSTGRES_HOST,
            "PORT": port,
            "CONN_MAX_AGE": DB_CONN_MAX_AGE,
            "CONN_HEALTH_CHECKS": True,
        }
    # Preserve stricter SSL options explicitly supplied by managed PostgreSQL URLs.
    options = database.setdefault("OPTIONS", {})
    if POSTGRES_SSLMODE_RAW:
        options["sslmode"] = POSTGRES_SSLMODE
    else:
        options.setdefault("sslmode", POSTGRES_SSLMODE)
    options.setdefault("connect_timeout", POSTGRES_CONNECT_TIMEOUT_SECONDS)
    DATABASES = {"default": database}
else:
    if not DEBUG and not env_bool("ALLOW_LEGACY_PRODUCTION_SQLITE", False):
        raise ImproperlyConfigured(
            "Production needs PostgreSQL: set DATABASE_URL or POSTGRES_HOST and "
            "POSTGRES_DB/POSTGRES_USER/POSTGRES_PASSWORD. Do not deploy before "
            "backing up and migrating any existing SQLite data. Existing "
            "SQLite-only hosts may temporarily use ALLOW_LEGACY_PRODUCTION_SQLITE=True."
        )
    SQLITE_DATABASE_PATH = Path(
        env("SQLITE_DATABASE_PATH") or str(BASE_DIR / "db.sqlite3")
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

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}
MEDIA_URL = "/media/"
# Public company logos must live on persistent storage in production.
MEDIA_ROOT = Path(env("DJANGO_MEDIA_ROOT", str(BASE_DIR / "media"))).expanduser()
if not MEDIA_ROOT.is_absolute():
    MEDIA_ROOT = BASE_DIR / MEDIA_ROOT
# Outside MEDIA_ROOT: public /media/ routes must never expose private job photos.
PRIVATE_JOB_MEDIA_ROOT = Path(env("PRIVATE_JOB_MEDIA_ROOT", str(BASE_DIR / "private_job_media"))).expanduser()
if not PRIVATE_JOB_MEDIA_ROOT.is_absolute():
    PRIVATE_JOB_MEDIA_ROOT = BASE_DIR / PRIVATE_JOB_MEDIA_ROOT

# Private inspection media may never be placed within a publicly served media
# or static directory, including through a symlink. Fail at Django startup.
public_media_path = MEDIA_ROOT.resolve()
static_root_path = STATIC_ROOT.resolve()
private_media_path = PRIVATE_JOB_MEDIA_ROOT.resolve()
if (private_media_path == public_media_path
        or public_media_path in private_media_path.parents
        or private_media_path == static_root_path
        or static_root_path in private_media_path.parents):
    raise ImproperlyConfigured(
        "PRIVATE_JOB_MEDIA_ROOT must be outside DJANGO_MEDIA_ROOT and STATIC_ROOT."
    )

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "accounts.User"

# Official Meta account onboarding is disabled without explicit configuration.
STAFF_PASSWORD_LINK_SIGNING_KEY = env("STAFF_PASSWORD_LINK_SIGNING_KEY", SECRET_KEY)
# Optional verified platform sender for first owner signup (not a new workshop's account).
WHATSAPP_PLATFORM_PHONE_ID = env("WHATSAPP_PLATFORM_PHONE_ID", "")
WHATSAPP_PLATFORM_ACCESS_TOKEN = env("WHATSAPP_PLATFORM_ACCESS_TOKEN", "")
WHATSAPP_PLATFORM_TEMPLATE = env("WHATSAPP_PLATFORM_TEMPLATE", "")
WHATSAPP_PLATFORM_TEMPLATE_LANGUAGE = env("WHATSAPP_PLATFORM_TEMPLATE_LANGUAGE", "en_US")

WHATSAPP_META_APP_ID = env("WHATSAPP_META_APP_ID", "")
WHATSAPP_META_APP_SECRET = env("WHATSAPP_META_APP_SECRET", "")
WHATSAPP_META_CONFIG_ID = env("WHATSAPP_META_CONFIG_ID", "")
WHATSAPP_WEBHOOK_VERIFY_TOKEN = env("WHATSAPP_WEBHOOK_VERIFY_TOKEN", "")
WHATSAPP_TOKEN_ENCRYPTION_KEY = env("WHATSAPP_TOKEN_ENCRYPTION_KEY", "")
WHATSAPP_GRAPH_VERSION = env("WHATSAPP_GRAPH_VERSION", "v25.0")
WHATSAPP_SEND_ENABLED = env_bool("WHATSAPP_SEND_ENABLED", False)
# Dedicated explicit opt-in. No automatic messages with unverified WABA/consent.
STAFF_WHATSAPP_AUTO_SEND_ENABLED = env_bool("STAFF_WHATSAPP_AUTO_SEND_ENABLED", False)

CUSTOMER_PORTAL_SIGNING_KEY = env("CUSTOMER_PORTAL_SIGNING_KEY", SECRET_KEY)
CUSTOMER_PORTAL_BASE_URL = env("CUSTOMER_PORTAL_BASE_URL", "http://localhost:5174/access" if DEBUG else "https://customer.cubegears.com/access")
if not DEBUG and not CUSTOMER_PORTAL_BASE_URL.startswith("https://"):
    raise ImproperlyConfigured("CUSTOMER_PORTAL_BASE_URL must use HTTPS in production.")

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
    "DEFAULT_THROTTLE_RATES": {"customer_portal": "30/min", "customer_website": "180/min"},
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
# Expose only the opaque request reference to cross-origin browser clients.
CORS_EXPOSE_HEADERS = ["X-Request-ID"]

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
# All application email is Celery-only. When disabled or broker is missing,
# account links and notifications cannot be sent; there is no SMTP fallback.
EMAIL_CELERY_ENABLED = env_bool("EMAIL_CELERY_ENABLED", False)
# Explicit optional synchronous SMTP transport for hosts without background workers.
# Keep disabled by default; enable only after verified SMTP configuration.
ACCOUNT_EMAIL_DELIVERY_MODE = env("ACCOUNT_EMAIL_DELIVERY_MODE", "disabled").strip().lower()
if ACCOUNT_EMAIL_DELIVERY_MODE not in {"disabled", "smtp", "celery"}:
    raise ImproperlyConfigured("ACCOUNT_EMAIL_DELIVERY_MODE must be disabled, smtp or celery.")

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


# The configured app is exposed through config.celery_app for Django and CLI.
# Redis, worker processes and optional Beat run separately from WSGI/ASGI.
# Email and billing flags remain explicit opt-ins; importing Celery starts no jobs.
CREDIT_BILLING_CELERY_ENABLED = env_bool("CREDIT_BILLING_CELERY_ENABLED", False)
CELERY_BROKER_URL = env("CELERY_BROKER_URL", "")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", "")
REQUIRE_REDIS_READINESS = env_bool("REQUIRE_REDIS_READINESS", False)
if REQUIRE_REDIS_READINESS and not CELERY_BROKER_URL.startswith(("redis://", "rediss://")):
    raise ImproperlyConfigured(
        "REQUIRE_REDIS_READINESS needs CELERY_BROKER_URL with redis:// or rediss://."
    )
# Web, Worker and Beat run in different OS processes/containers. Fail
# explicitly if the worker/scheduler is deployed without its queue broker.
SERVICE_ROLE = env("SERVICE_ROLE", "web").strip().lower()
if SERVICE_ROLE not in {"web", "worker", "beat"}:
    raise ImproperlyConfigured("SERVICE_ROLE must be web, worker or beat.")
if SERVICE_ROLE in {"worker", "beat"} and not CELERY_BROKER_URL.startswith(("redis://", "rediss://")):
    raise ImproperlyConfigured(
        "Separate Celery workers and Beat require a Redis CELERY_BROKER_URL."
    )
CELERY_TIMEZONE = TIME_ZONE
CELERY_ENABLE_UTC = True
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_TASK_IGNORE_RESULT = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_BEAT_SCHEDULE = {}
# Both Beat and the broker-free Scheduled Task runner share this opt-in flag.
ATTENDANCE_AUTO_CHECKOUT_ENABLED = env_bool("ATTENDANCE_AUTO_CHECKOUT_ENABLED", False)
if CREDIT_BILLING_CELERY_ENABLED:
    if not CELERY_BROKER_URL.startswith(("redis://", "rediss://")):
        from django.core.exceptions import ImproperlyConfigured
        raise ImproperlyConfigured(
            "CREDIT_BILLING_CELERY_ENABLED requires an explicit Redis CELERY_BROKER_URL."
        )
    from celery.schedules import crontab
    CELERY_BEAT_SCHEDULE = {
        "cubegear-renew-credit-features": {
            "task": "credits.renew_due_features",
            "schedule": crontab(hour=2, minute=5),
        },
        "cubegear-bill-storage-period": {
            "task": "credits.bill_due_storage",
            "schedule": crontab(hour=2, minute=15),
        },
        "cubegear-meter-storage": {
            "task": "credits.meter_postpaid_storage",
            "schedule": crontab(minute="*"),
        },
        "cubegear-sync-postpaid-seat-rates": {
            "task": "credits.sync_postpaid_feature_rates",
            "schedule": crontab(minute="*/5"),
        },
        "cubegear-monthly-usage-settlement": {
            "task": "credits.settle_previous_month",
            "schedule": crontab(day_of_month=1, hour=0, minute=20),
        },
    }

# The calendar task is safe when the platform policy is disabled. It runs daily
# to finalize closed months and retry due credit withdrawals after recharge.
if CREDIT_BILLING_CELERY_ENABLED:
    CELERY_BEAT_SCHEDULE["cubegear-settle-calendar-credit-statements"] = {
        "task": "credits.settle_calendar_cycles",
        "schedule": crontab(hour=0, minute=10),
    }

# Razorpay SaaS subscription checkout. Configure TEST keys before enabling.
RAZORPAY_KEY_ID = env("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = env("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET = env("RAZORPAY_WEBHOOK_SECRET", "")
# Optional wildcard frontend hostname; provisioning DNS/TLS is a deployment step.
TENANT_BASE_DOMAIN = env("TENANT_BASE_DOMAIN", "")

CREDIT_FEATURE_ENFORCEMENT_ENABLED = env_bool("CREDIT_FEATURE_ENFORCEMENT_ENABLED", False)
# Opt-in: non-core workshop APIs require an ACTIVE purchased feature label.
# Existing tenants need label entitlements and operator approval before rollout.
CREDIT_LABEL_ONLY_ACCESS_ENABLED = env_bool("CREDIT_LABEL_ONLY_ACCESS_ENABLED", False)
# Phase-gated: deny any unregistered tenant modules, while keeping core/auth/billing accessible.
# Turn on only after catalog and all tenant entitlements have been backfilled.
CREDIT_STRICT_MODULE_GATING_ENABLED = env_bool("CREDIT_STRICT_MODULE_GATING_ENABLED", False)
# Opt-in replacement for individual FEATURE prepaid renewal. Existing accounts
# require migration into usage sessions before enabling on production.
CREDIT_POSTPAID_FEATURE_BILLING_ENABLED = env_bool("CREDIT_POSTPAID_FEATURE_BILLING_ENABLED", False)
# Legacy metered-month billing is disabled unless explicitly enabled with
# an audited broker/worker; do not run two feature billing engines together.
CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED = env_bool("CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED", False)
STORAGE_MONTHLY_METERED_ENABLED = env_bool("STORAGE_MONTHLY_METERED_ENABLED", False)
STORAGE_METER_INTERVAL_SECONDS = int(env("STORAGE_METER_INTERVAL_SECONDS", 300))
CREDIT_FEATURE_STRICT_GATE_ENABLED = env_bool(
    "CREDIT_FEATURE_STRICT_GATE_ENABLED",
    CREDIT_STRICT_MODULE_GATING_ENABLED,
)
# Old subscription paywall is retired. Legacy transactions remain for audit history.


# The newer monthly usage engines are independent of prepaid renewals.
# Register each sampler and previous-month settlement only when its own
# enforcement/billing switch is enabled. Do not schedule legacy equivalents.
if CREDIT_BILLING_CELERY_ENABLED:
    if CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED:
        CELERY_BEAT_SCHEDULE.pop("cubegear-renew-credit-features", None)
        CELERY_BEAT_SCHEDULE.pop("cubegear-sync-postpaid-seat-rates", None)
        CELERY_BEAT_SCHEDULE.pop("cubegear-monthly-usage-settlement", None)
        CELERY_BEAT_SCHEDULE.update({
            "cubegear-sample-paid-features": {
                "task": "cubegear.features.monthly.sample",
                "schedule": crontab(minute="*/5"),
            },
            "cubegear-settle-closed-feature-months": {
                "task": "cubegear.features.monthly.settle",
                "schedule": crontab(hour=0, minute=25),
            },
        })
    if STORAGE_MONTHLY_METERED_ENABLED:
        CELERY_BEAT_SCHEDULE.pop("cubegear-bill-storage-period", None)
        CELERY_BEAT_SCHEDULE.pop("cubegear-meter-storage", None)
        CELERY_BEAT_SCHEDULE.pop("cubegear-monthly-usage-settlement", None)
        CELERY_BEAT_SCHEDULE.update({
            "cubegear-sample-company-storage": {
                "task": "cubegear.storage.monthly.sample",
                "schedule": crontab(minute="*"),
            },
            "cubegear-settle-closed-storage-months": {
                "task": "cubegear.storage.monthly.settle",
                "schedule": crontab(hour=0, minute=30),
            },
        })


# The new opt-in billing policy is database-configured by platform staff, so
# one recurring (hourly) task retries due invoices after verified top-ups.
# The task is a no-op unless the admin has explicitly enabled the policy.
if CREDIT_BILLING_CELERY_ENABLED:
    CELERY_BEAT_SCHEDULE["cubegear-settle-configurable-credit-cycles"] = {
        "task": "credits.settle_due_cycles",
        "schedule": crontab(minute=15),
    }

# Email notices use a persisted outbox; billing charges never depend on SMTP.
# Switch both flags on only after Redis, a single Celery Beat and SMTP are ready.
BILLING_EMAIL_ALERTS_ENABLED = env_bool("BILLING_EMAIL_ALERTS_ENABLED", False)
if CREDIT_BILLING_CELERY_ENABLED and EMAIL_CELERY_ENABLED and BILLING_EMAIL_ALERTS_ENABLED:
    CELERY_BEAT_SCHEDULE["cubegear-billing-email-outbox"] = {
        "task": "cubegear.billing.send_monthly_notices",
        "schedule": crontab(minute="*/10"),
    }

if CREDIT_BILLING_CELERY_ENABLED:
    CELERY_BEAT_SCHEDULE["cubegear-monthly-due-date-collection"] = {
        "task": "credits.collect_due_monthly_bills",
        "schedule": crontab(minute=35),
    }


# Heartbeat reads admin intervals/due time from the database at execution time.
# A single Beat process is required. Legacy tasks remain gated/idempotent.
if CREDIT_BILLING_CELERY_ENABLED:
    CELERY_BEAT_SCHEDULE["cubegear-configured-billing"] = {
        "task": "credits.run_configured_billing",
        "schedule": 60.0,
    }

# Storage monitoring is read-only and can run independently of credit billing.
# It is opt-in at the database level, and its heartbeat never debits wallets.
# Redis + exactly one Celery worker and one Beat instance are required.
if CELERY_BROKER_URL.startswith(("redis://", "rediss://")):
    from celery.schedules import crontab as monitoring_crontab
    CELERY_BEAT_SCHEDULE["cubegear-storage-monitoring-snapshots"] = {
        "task": "cubegear.storage.monitoring.snapshot_due",
        "schedule": monitoring_crontab(minute="*"),
    }

# Only a single scheduler may execute automatic attendance checkout. If using
# PythonAnywhere scheduled management commands, do NOT run this Beat schedule.
if ATTENDANCE_AUTO_CHECKOUT_ENABLED and CELERY_BROKER_URL.startswith(("redis://", "rediss://")):
    from celery.schedules import crontab as attendance_crontab
    CELERY_BEAT_SCHEDULE["cubegear-attendance-auto-checkout"] = {
        "task": "apps.attendance.tasks.auto_close_attendance_sessions",
        "schedule": attendance_crontab(minute="*/5"),
    }
