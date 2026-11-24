"""Revocable long-lived customer tracking capability; no raw token in DB."""
import hashlib
import hmac
import os
import secrets
import uuid
from urllib.parse import urlsplit
from django.conf import settings
from django.db.models import F
from django.utils import timezone
from apps.companies.models import CompanyWebsiteSubdomain
from apps.companies.subdomains import (
    LABEL_PATTERN, customer_website_url, ensure_default_subdomain, subdomain_label,
)
from .models import CustomerPortalAccess


class WebsiteDisabled(ValueError):
    pass


class InvalidDevelopmentOrigin(ValueError):
    pass


def _development_origin(value):
    if not isinstance(value, str) or len(value) > 64:
        raise InvalidDevelopmentOrigin
    try:
        url = urlsplit(value)
        if (url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1"}
                and url.username is None and url.password is None
                and url.path in {"", "/"} and not url.query and not url.fragment):
            # Reading .port validates malformed and out-of-range ports.
            if url.port != 0:
                return url
    except ValueError as error:
        raise InvalidDevelopmentOrigin from error
    raise InvalidDevelopmentOrigin


def _token(access):
    key = str(getattr(settings, "CUSTOMER_PORTAL_SIGNING_KEY", settings.SECRET_KEY)).encode()
    data = f"customer:v1:{access.pk.hex}:{access.company_id}:{access.customer_id}:{access.nonce}".encode()
    signature = hmac.new(key, data, hashlib.sha256).hexdigest()
    return f"{access.pk.hex}.{signature}"


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def ensure_portal_access(customer):
    record, _ = CustomerPortalAccess.objects.get_or_create(
        customer=customer,
        defaults={"company": customer.company, "branch": customer.branch},
    )
    if not record.token_hash:
        record.token_hash = digest(_token(record))
        record.save(update_fields=["token_hash", "updated_at"])
    return record


def issue_link(access, *, development_origin=None):
    if not access.company.is_active or CompanyWebsiteSubdomain.objects.filter(
        company=access.company, website_enabled=False,
    ).exists():
        raise WebsiteDisabled("The customer website is disabled.")
    token = _token(access)
    if not hmac.compare_digest(digest(token), access.token_hash or ""):
        raise ValueError("Tracking link cannot be recovered. Regenerate it.")

    # Development links must stay on the exact local workshop website.
    # Do this BEFORE production-domain checks: enabling the production wildcard
    # feature flag in a local environment must not send customers to production.
    base = getattr(settings, "CUSTOMER_PORTAL_BASE_URL", "http://localhost/access").rstrip("/")
    if settings.DEBUG:
        parsed = _development_origin(development_origin) if development_origin is not None else urlsplit(base)
        if parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}:
            label = subdomain_label(access.company)
            if LABEL_PATTERN.fullmatch(label):
                port = f":{parsed.port}" if parsed.port else ""
                return f"http://{label}.localhost{port}/access/{token}"

    # Use a tenant-specific production subdomain only after wildcard DNS/TLS
    # have been explicitly enabled.
    if os.environ.get("CUSTOMER_WEBSITE_LINKS_ENABLED", "").lower() == "true":
        label = ensure_default_subdomain(access.company).label
        if LABEL_PATTERN.fullmatch(label):
            return f"{customer_website_url(label)}access/{token}"

    # Transitional fallback stays on the configured Next.js /access route.
    if base.endswith("/c"):
        base = base[:-2] + "/access"
    elif not base.endswith("/access"):
        base += "/access"
    return f"{base}/{token}"


def revoke_link(access):
    access.is_active = False
    access.revoked_at = timezone.now()
    access.save(update_fields=["is_active", "revoked_at", "updated_at"])


def rotate_link(access, *, development_origin=None):
    access.nonce = secrets.token_hex(32)
    access.token_hash = digest(_token(access))
    access.is_active = True
    access.rotated_at = timezone.now()
    access.revoked_at = None
    url = issue_link(access, development_origin=development_origin)
    access.save(update_fields=["nonce", "token_hash", "is_active", "rotated_at", "revoked_at", "updated_at"])
    return url


def resolve_link(raw_token):
    if not isinstance(raw_token, str) or len(raw_token) != 97:
        return None
    try:
        raw_id, mac = raw_token.split(".", 1)
        if len(raw_id) != 32 or len(mac) != 64:
            return None
        access_id = uuid.UUID(hex=raw_id)
    except (ValueError, TypeError):
        return None
    access = CustomerPortalAccess.objects.select_related("customer", "company").filter(
        id=access_id, is_active=True, customer__status="active",
        company__is_active=True, customer__company_id=F("company_id"),
    ).exclude(company__website_subdomain__website_enabled=False).first()
    if not access:
        return None
    expected = _token(access)
    if not (hmac.compare_digest(raw_token, expected)
            and hmac.compare_digest(digest(raw_token), access.token_hash or "")):
        return None
    return access
