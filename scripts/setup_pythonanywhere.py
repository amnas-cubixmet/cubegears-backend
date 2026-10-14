#!/usr/bin/env python3
"""Safely configure a persistent production .env on PythonAnywhere.

Run on the SERVER, not in GitHub Actions. Never prints secret values and
never replaces an existing usable secret. Does not require Celery.
"""
import argparse
import os
import re
import secrets
import sys
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values, set_key


SAMPLE_KEYS = {
    "dev-only-change-me",
    "change-this-in-production-use-at-least-32-random-bytes",
    "change-this-jwt-signing-key-use-at-least-32-random-bytes",
    "YOUR_GENERATED_SECRET_KEY",
    "PASTE_YOUR_GENERATED_SECRET_KEY",
}


def valid_secret(value):
    value = value or ""
    return (
        len(value.encode("utf-8")) >= 32
        and value not in SAMPLE_KEYS
        and not value.startswith(("change-this-", "replace-with-", "<"))
    )


def validate_host(host):
    """Only a single hostname; not a URL or wildcard."""
    host = host.strip().lower()
    if (
        len(host) > 253
        or not host
        or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part)
               for part in host.split("."))
    ):
        raise ValueError("Pass a hostname only, e.g. cardflow.pythonanywhere.com")
    return host


def normalize_origin(value):
    parsed = urlsplit(value)
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Frontend must be an HTTPS origin, e.g. https://app.example.com")
    return f"https://{parsed.netloc.lower()}"


def add_csv(existing, item):
    values = [entry.strip() for entry in (existing or "").split(",") if entry.strip()]
    if item not in values:
        values.append(item)
    return ",".join(values)


def ensure_file_private(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    # 0600 protects newly created files; chmod also hardens existing .env files.
    fd = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
    os.close(fd)
    path.chmod(0o600)


def configure(env_path, host, frontend_url=None):
    """Return only non-secret setup information. Preserve valid existing keys."""
    env_path = Path(env_path)
    host = validate_host(host)
    frontend = normalize_origin(frontend_url) if frontend_url else None
    ensure_file_private(env_path)
    config = dict(dotenv_values(env_path))
    statuses = {}

    def set_env(name, value):
        set_key(str(env_path), name, value, quote_mode="auto")
        config[name] = value

    existing_django = config.get("DJANGO_SECRET_KEY")
    if valid_secret(existing_django):
        statuses["DJANGO_SECRET_KEY"] = "preserved existing valid key"
    else:
        set_env("DJANGO_SECRET_KEY", secrets.token_urlsafe(48))
        statuses["DJANGO_SECRET_KEY"] = "generated strong private key"

    existing_jwt = config.get("JWT_SIGNING_KEY")
    if valid_secret(existing_jwt):
        statuses["JWT_SIGNING_KEY"] = "preserved existing valid key"
    elif not existing_jwt and valid_secret(existing_django):
        # Preserve historical Django fallback signing key so existing JWTs
        # are not silently invalidated by adding a different key.
        statuses["JWT_SIGNING_KEY"] = "existing Django fallback preserved"
    else:
        set_env("JWT_SIGNING_KEY", secrets.token_urlsafe(48))
        statuses["JWT_SIGNING_KEY"] = "generated strong private key"

    set_env("DJANGO_DEBUG", "False")
    set_env("DJANGO_ALLOWED_HOSTS", add_csv(config.get("DJANGO_ALLOWED_HOSTS"), host))
    set_env(
        "CSRF_TRUSTED_ORIGINS",
        add_csv(config.get("CSRF_TRUSTED_ORIGINS"), f"https://{host}"),
    )

    if frontend:
        set_env("FRONTEND_URL", frontend)
        set_env("CORS_ALLOWED_ORIGINS", add_csv(config.get("CORS_ALLOWED_ORIGINS"), frontend))
        set_env("CSRF_TRUSTED_ORIGINS", add_csv(config.get("CSRF_TRUSTED_ORIGINS"), frontend))

    if config.get("EMAIL_BACKEND") in {
        None, "", "django.core.mail.backends.console.EmailBackend"
    }:
        set_env("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
        statuses["EMAIL_BACKEND"] = "SMTP enabled; credentials still required"

    ensure_file_private(env_path)
    return statuses


def environment_warnings(env_path):
    """Spot overrides: python-dotenv never overwrites already-exported values."""
    values = dotenv_values(env_path)
    warnings = []
    for name in ("DJANGO_SECRET_KEY", "JWT_SIGNING_KEY"):
        exported = os.environ.get(name)
        if exported is not None and not valid_secret(exported):
            warnings.append(
                f"{name} is set to an invalid value in the shell environment; "
                "unset it or fix the PythonAnywhere WSGI environment before running Django."
            )
        elif exported is not None and values.get(name) != exported:
            warnings.append(
                f"{name} in the shell environment differs from .env and overrides it. "
                "Ensure WSGI and Bash use the same signing key."
            )
    if os.environ.get("DJANGO_DEBUG", "").lower() in {"true", "1", "yes", "on"}:
        warnings.append("DJANGO_DEBUG is exported as True and overrides .env.")
    if os.environ.get("DJANGO_ALLOWED_HOSTS") and (
        os.environ["DJANGO_ALLOWED_HOSTS"] != values.get("DJANGO_ALLOWED_HOSTS")
    ):
        warnings.append("DJANGO_ALLOWED_HOSTS is exported and overrides .env; verify production host.")
    if not values.get("FRONTEND_URL") or "localhost" in values.get("FRONTEND_URL", ""):
        warnings.append(
            "FRONTEND_URL still points to localhost. Set --frontend-url to your HTTPS frontend."
        )
    if not values.get("EMAIL_HOST") or not values.get("EMAIL_HOST_USER") or not values.get("EMAIL_HOST_PASSWORD"):
        warnings.append(
            "SMTP credentials are missing: configure EMAIL_HOST, EMAIL_HOST_USER, "
            "EMAIL_HOST_PASSWORD, and a verified DEFAULT_FROM_EMAIL privately in .env."
        )
    return warnings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="Backend hostname; no https:// prefix")
    parser.add_argument("--frontend-url", help="Public HTTPS frontend origin (optional)")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(__file__).resolve().parent.parent / ".env",
        help="Private Django environment file, default: repo/.env",
    )
    args = parser.parse_args(argv)
    try:
        statuses = configure(args.env_file, args.host, args.frontend_url)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"Configuration failed: {exc}\n")
    print(f"Protected production configuration written to: {args.env_file}")
    for name, result in statuses.items():
        print(f"{name}: {result}")
    print("DJANGO_DEBUG=False; allowed host and backend CSRF origin configured.")
    warnings = environment_warnings(args.env_file)
    for message in warnings:
        print(f"WARNING: {message}", file=sys.stderr)
    if any(("shell environment" in message or "overrides .env" in message or "differs from .env" in message) for message in warnings):
        return 2
    print("Next: python manage.py check && python manage.py migrate --noinput")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
