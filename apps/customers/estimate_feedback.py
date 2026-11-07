"""Version-scoped customer estimate feedback: validated, minimal and auditable.

Reason codes are stable identifiers, not user-controlled notification text.
Do not treat feedback as an estimate edit or approval.
"""
from rest_framework.exceptions import ValidationError

DECLINE_REASONS = frozenset({
    "price", "items", "timing", "explanation", "other", "unspecified",
})
PUBLIC_REASONS = DECLINE_REASONS - {"unspecified"}
MAX_DECLINE_NOTE_LENGTH = 500


def validated_estimate_feedback(data, decision):
    """Old portal clients without reason stay compatible; current UI requires one."""
    if decision == "accept":
        if data.get("declineReason") not in (None, "") or data.get("declineNote") not in (None, ""):
            raise ValidationError({"declineReason": "Acceptance cannot include decline feedback."})
        return None

    raw_reason = data.get("declineReason", "unspecified")
    reason = str(raw_reason or "").strip()
    if reason not in DECLINE_REASONS:
        raise ValidationError({"declineReason": "Choose a supported decline reason."})
    raw_note = data.get("declineNote") or ""
    if not isinstance(raw_note, str):
        raise ValidationError({"declineNote": "Feedback must be plain text."})
    note = raw_note.strip()
    if len(note) > MAX_DECLINE_NOTE_LENGTH:
        raise ValidationError({"declineNote": "Feedback must be at most 500 characters."})
    return {"reason": reason, "note": note}


def public_decline_feedback(record):
    """Project only explicitly allowed customer-authored decline fields."""
    if not isinstance(record, dict):
        return None
    reason = record.get("declineReason", "unspecified")
    note = record.get("declineNote", "")
    return {
        "reason": reason if reason in DECLINE_REASONS else "unspecified",
        "note": note if isinstance(note, str) and len(note) <= MAX_DECLINE_NOTE_LENGTH else "",
    }
