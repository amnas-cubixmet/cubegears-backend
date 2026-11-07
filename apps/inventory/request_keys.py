"""Idempotency for user-triggered inventory balance mutations."""
import re

from rest_framework.exceptions import ValidationError

from .models import StockMovement


def stock_request_key(request):
    key = str(
        request.data.get("requestKey")
        or request.data.get("idempotencyKey")
        or request.data.get("idempotency_key")
        or request.headers.get("Idempotency-Key")
        or ""
    ).strip()
    if key and (len(key) < 8 or len(key) > 120 or not re.fullmatch(r"[A-Za-z0-9_.:-]+", key)):
        raise ValidationError({"requestKey": "Use a unique ASCII request key (8–120 characters)."})
    return key or None


def replay_movement(request, key, *, item, kind, quantity):
    if not key:
        return None
    entry = StockMovement.objects.filter(
        company_id=request.user.company_id, request_key=key,
    ).first()
    if not entry:
        return None
    if entry.item_id != item.pk or entry.movement_type != kind or entry.quantity != quantity:
        raise ValidationError({"requestKey": "This request key belongs to a different inventory transaction."})
    return entry
