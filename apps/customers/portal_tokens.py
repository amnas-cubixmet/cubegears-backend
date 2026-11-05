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
    # Switch staff-generated links to the dedicated customer website only once
    # the wildcard DNS and TLS deployment are actually working.
    import os
    import re
    if os.environ.get("CUSTOMER_WEBSITE_LINKS_ENABLED", "").lower() == "true":
        from apps.companies.models import CompanyDomain
        root = os.environ.get("CUSTOMER_PORTAL_ROOT_DOMAIN", "cubegears.com").lower().strip(".")
        preferred = CompanyDomain.objects.filter(
            company=access.company, status=CompanyDomain.ACTIVE,
            is_primary=True, is_approved=True,
            dns_configured=True, ownership_verified=True, ssl_status="ready",
            verified_at__isnull=False,
        ).first()
        if preferred:
            return f"https://{preferred.hostname}/access/{token}"
        if re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", access.company.slug):
            return f"https://{access.company.slug}.{root}/access/{token}"
    base = getattr(settings, "CUSTOMER_PORTAL_BASE_URL", "http://localhost:5173/c").rstrip("/")
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
