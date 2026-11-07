"""Retry-safe estimate identity stored inside the existing pricing snapshot.

No new schema is introduced. Estimate versions and monetary values are
calculated only by Django; the browser supplies a stable operation UUID.
"""

import hashlib
import json
from uuid import UUID

from rest_framework.exceptions import ValidationError


def estimate_identity(payload):
    raw = payload.get("requestId")
    if raw in (None, ""):
        return None  # Existing clients may continue without a token.
    try:
        normalized = str(UUID(str(raw)))
    except (TypeError, ValueError, AttributeError):
        raise ValidationError({"requestId": "Provide a valid UUID requestId for this estimate operation."})
    # The same request ID must not be reused to submit different quote content.
    content = {key: value for key, value in payload.items() if key != "requestId"}
    fingerprint = hashlib.sha256(json.dumps(
        content, sort_keys=True, ensure_ascii=True, default=str,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return normalized, fingerprint


def saved_estimate_identity(estimate):
    for entry in reversed(estimate.items or []):
        if isinstance(entry, dict) and isinstance(entry.get("_pricing"), dict):
            pricing = entry["_pricing"]
            if pricing.get("requestId"):
                return pricing["requestId"], pricing.get("requestFingerprint")
    return None


def attach_identity(items, identity):
    if not identity:
        return
    if not items or not isinstance(items[-1], dict) or "_pricing" not in items[-1]:
        raise ValidationError({"estimate": "The estimate pricing snapshot is missing."})
    items[-1]["_pricing"]["requestId"] = identity[0]
    items[-1]["_pricing"]["requestFingerprint"] = identity[1]
