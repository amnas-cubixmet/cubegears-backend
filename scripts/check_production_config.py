#!/usr/bin/env python3
"""Safe production readiness check (no secret values printed, no changes made).

Usage:
  python scripts/check_production_config.py
  python scripts/check_production_config.py --connect-db --connect-redis
"""
import argparse
import os
import sys
from urllib.parse import urlsplit

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connect-db", action="store_true", help="Test database connection (read only)")
    parser.add_argument("--connect-redis", action="store_true", help="Ping Redis broker (read only)")
    args = parser.parse_args(argv)

    try:
        import django
        django.setup()
        from django.conf import settings
        from django.db import connections
    except Exception as exc:
        print(f"ERROR: Django production settings are invalid ({type(exc).__name__}).", file=sys.stderr)
        return 1

    errors = []
    if settings.DEBUG:
        errors.append("DJANGO_DEBUG must be False.")
    if settings.DATABASES["default"]["ENGINE"] != "django.db.backends.postgresql":
        errors.append("Production database must be PostgreSQL, not SQLite.")
    if not settings.ALLOWED_HOSTS or "*" in settings.ALLOWED_HOSTS:
        errors.append("DJANGO_ALLOWED_HOSTS must be explicit (no *).")
    if not settings.SECURE_SSL_REDIRECT or not settings.SESSION_COOKIE_SECURE:
        errors.append("Enforce HTTPS redirect and secure session cookies.")
    if not settings.CSRF_COOKIE_SECURE:
        errors.append("CSRF_COOKIE_SECURE must be True.")
    if not settings.CORS_ALLOWED_ORIGINS or any(
        not value.startswith("https://") for value in settings.CORS_ALLOWED_ORIGINS
    ):
        errors.append("CORS_ALLOWED_ORIGINS must contain production HTTPS origins only.")
    if not settings.CSRF_TRUSTED_ORIGINS or any(
        not value.startswith("https://") for value in settings.CSRF_TRUSTED_ORIGINS
    ):
        errors.append("CSRF_TRUSTED_ORIGINS must contain production HTTPS origins.")
    frontend = urlsplit(settings.FRONTEND_URL)
    if frontend.scheme != "https" or not frontend.hostname:
        errors.append("FRONTEND_URL must be HTTPS.")
    broker = settings.CELERY_BROKER_URL or ""
    if not broker.startswith(("redis://", "rediss://")):
        errors.append("CELERY_BROKER_URL must point to separate Redis.")
    if not str(settings.MEDIA_ROOT).startswith("/var/lib/") and os.name == "posix":
        print("WARNING: Confirm DJANGO_MEDIA_ROOT is backed by persistent storage.", file=sys.stderr)

    if errors:
        for item in errors:
            print("ERROR: " + item, file=sys.stderr)
        return 1

    if args.connect_db:
        try:
            with connections["default"].cursor() as cursor:
                cursor.execute("SELECT 1")
                if cursor.fetchone()[0] != 1:
                    raise RuntimeError("Unhealthy database response")
        except Exception:
            print("ERROR: PostgreSQL connection failed. Check private credentials/host/TLS.", file=sys.stderr)
            return 1
        print("PostgreSQL connectivity: OK")

    if args.connect_redis:
        try:
            from redis import Redis
            client = Redis.from_url(broker, socket_connect_timeout=5, socket_timeout=5)
            if client.ping() is not True:
                raise RuntimeError("Unexpected Redis result")
        except Exception:
            print("ERROR: Redis broker is unreachable or rejected authentication.", file=sys.stderr)
            return 1
        print("Redis broker connectivity: OK")

    print("Production config: OK (secrets not displayed).")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
