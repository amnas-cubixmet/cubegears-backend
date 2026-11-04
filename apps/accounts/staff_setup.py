"""Staff account one-use setup/reset links; HMAC derivation allows Celery to render
a link without putting raw tokens into task arguments or storing plaintext.
"""
import hashlib
import hmac
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from django.utils import timezone
from .models import MagicLink, StaffPasswordInvitation
from .serializers import user_can_sign_in


def _token(record):
    key = str(getattr(settings, "STAFF_PASSWORD_LINK_SIGNING_KEY", settings.SECRET_KEY)).encode()
    body = f"staff-setup:v1:{record.pk.hex}:{record.user_id}:{record.nonce}:{record.purpose}".encode()
    mac = hmac.new(key, body, hashlib.sha256).hexdigest()
    return record.pk.hex + "." + mac


def _digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def invitation_url(record):
    url = getattr(settings, "FRONTEND_URL", "http://localhost:5173").rstrip("/")
    raw = _token(record)
    if not hmac.compare_digest(_digest(raw), record.token_hash):
        raise ValueError("Invitation signing key changed; issue a new invitation.")
    return f"{url}/set-password/{raw}"


@transaction.atomic
def issue_invitation(user, issuer=None, purpose=None):
    """Invalidate all older links, including pending resets; only latest works."""
    if not user.company_id or not user.is_active:
        raise ValueError("Staff login account is disabled or has no workshop.")
    if not user.role or not user.role.is_active or user.role.company_id != user.company_id:
        raise ValueError("An active workshop role is required.")
    StaffPasswordInvitation.objects.select_for_update().filter(
        user=user, used_at__isnull=True, invalidated_at__isnull=True,
    ).update(invalidated_at=timezone.now(), delivery_status="superseded")
    record = StaffPasswordInvitation(
        user=user, issued_by=issuer, nonce=secrets.token_hex(32),
        purpose=purpose or ("reset" if user.has_usable_password() else "setup"),
        expires_at=timezone.now() + timedelta(hours=24),
    )
    record.token_hash = _digest(_token(record))
    record.save()
    return record


def resolve_invitation(raw):
    if not isinstance(raw, str) or len(raw) != 97:
        return None
    try:
        ident, signature = raw.split(".", 1)
        if len(ident) != 32 or len(signature) != 64:
            return None
        invitation_id = uuid.UUID(hex=ident)
    except (TypeError, ValueError):
        return None
    record = StaffPasswordInvitation.objects.select_related(
        "user", "user__role", "user__company",
    ).filter(
        pk=invitation_id, used_at__isnull=True, invalidated_at__isnull=True,
        expires_at__gt=timezone.now(),
    ).first()
    if not record or not user_can_sign_in(record.user):
        return None
    expected = _token(record)
    if not (hmac.compare_digest(raw, expected) and
            hmac.compare_digest(_digest(raw), record.token_hash)):
        return None
    if record.purpose == "setup" and record.user.has_usable_password():
        return None
    return record


@transaction.atomic
def consume_invitation(raw, password):
    validate_password(password)
    invitation = resolve_invitation(raw)
    if not invitation:
        return None
    record = StaffPasswordInvitation.objects.select_for_update().get(pk=invitation.pk)
    # Double check validity under DB row lock before consuming.
    if (record.used_at or record.invalidated_at or
        record.expires_at <= timezone.now() or
        not hmac.compare_digest(_digest(raw), record.token_hash)):
        return None
    user = record.user
    if not user_can_sign_in(user):
        return None
    user.set_password(password)
    user.save(update_fields=["password"])
    now = timezone.now()
    record.used_at = now
    record.delivery_status = "consumed"
    record.save(update_fields=["used_at", "delivery_status"])
    StaffPasswordInvitation.objects.filter(
        user=user, used_at__isnull=True, invalidated_at__isnull=True,
    ).exclude(pk=record.pk).update(invalidated_at=now)
    MagicLink.objects.filter(user=user, used_at__isnull=True).update(used_at=now)
    return user
