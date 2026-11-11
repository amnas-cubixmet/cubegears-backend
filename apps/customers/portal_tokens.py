"""Revocable long-lived customer tracking capability; no raw token in DB."""
import hashlib
import hmac
import secrets
import uuid
from django.conf import settings
from django.utils import timezone
from .models import CustomerPortalAccess


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


def issue_link(access):
    token = _token(access)
    if not hmac.compare_digest(digest(token), access.token_hash or ""):
        raise ValueError("Tracking link cannot be recovered. Regenerate it.")
    import os
    import re
    from urllib.parse import urlsplit
    from apps.companies.subdomains import subdomain_label

    # Development links must stay on the exact local workshop website.
    # Do this BEFORE production-domain checks: enabling the production wildcard
    # feature flag in a local environment must not send customers to production.
    base = getattr(settings, "CUSTOMER_PORTAL_BASE_URL", "http://localhost/access").rstrip("/")
    if settings.DEBUG:
        parsed = urlsplit(base)
        label = subdomain_label(access.company)
        if (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}
                and re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?", label)):
            port = f":{parsed.port}" if parsed.port else ""
            return f"http://{label}.localhost{port}/access/{token}"

    # Use a tenant-specific production subdomain only after wildcard DNS/TLS
    # have been explicitly enabled.
    if os.environ.get("CUSTOMER_WEBSITE_LINKS_ENABLED", "").lower() == "true":
        root = os.environ.get("CUSTOMER_PORTAL_ROOT_DOMAIN", "cubegears.com").lower().strip(".")
        label = subdomain_label(access.company)
        if re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?", label):
            return f"https://{label}.{root}/access/{token}"

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


def rotate_link(access):
    access.nonce = secrets.token_hex(32)
    access.token_hash = digest(_token(access))
    access.is_active = True
    access.rotated_at = timezone.now()
    access.revoked_at = None
    access.save(update_fields=["nonce", "token_hash", "is_active", "rotated_at", "revoked_at", "updated_at"])
    return issue_link(access)


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
        company__is_active=True,
    ).first()
    if not access:
        return None
    expected = _token(access)
    if not (hmac.compare_digest(raw_token, expected)
            and hmac.compare_digest(digest(raw_token), access.token_hash or "")):
        return None
    return access
