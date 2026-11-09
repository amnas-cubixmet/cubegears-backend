"""Stop the Windows development launcher before migrations if env is production.

This checks the *effective* Django configuration, not just the text in .env,
because OS environment variables can override local dotenv settings.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

    from django.conf import settings

    if not settings.DEBUG or settings.SECURE_SSL_REDIRECT:
        print(
            "[ERROR] This launcher is LOCAL DEVELOPMENT ONLY. "
            "Use DJANGO_DEBUG=True and SECURE_SSL_REDIRECT=False.",
            file=sys.stderr,
        )
        return 1

    database = settings.DATABASES["default"]
    engine = database["ENGINE"]
    if engine == "django.db.backends.postgresql":
        host = str(database.get("HOST") or "").strip().lower()
        if host not in {"127.0.0.1", "localhost", "::1", "postgres"}:
            print(
                "[ERROR] The configured PostgreSQL DB is remote. "
                "Refusing automatic migrations. Use a local development DB.",
                file=sys.stderr,
            )
            return 1
    elif engine != "django.db.backends.sqlite3":
        print("[ERROR] Unsupported local database backend.", file=sys.stderr)
        return 1

    broker = str(getattr(settings, "CELERY_BROKER_URL", "") or
                 os.getenv("CELERY_BROKER_URL") or "redis://127.0.0.1:6379/0")
    try:
        parsed = urlparse(broker)
        local_redis = (
            parsed.scheme in {"redis", "rediss"}
            and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        )
    except ValueError:
        local_redis = False
    if not local_redis:
        print(
            "[ERROR] Celery broker must point to local Redis for this launcher.",
            file=sys.stderr,
        )
        return 1

    print("[ok] Safe local Django database and Redis environment confirmed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
