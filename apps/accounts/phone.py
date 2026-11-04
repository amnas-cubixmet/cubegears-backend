"""Consistent E.164 phone identifiers for multi-tenant password login."""
import re
from django.core.exceptions import ValidationError


def normalize_phone(value, *, optional=False):
    raw = str(value or "").strip()
    if not raw and optional:
        return None
    # Accept spaces/parentheses/dashes only; never infer a country code.
    if raw.startswith("00"):
        raw = "+" + raw[2:]
    compact = re.sub(r"[\s().-]", "", raw)
    if not re.fullmatch(r"\+[1-9][0-9]{7,14}", compact):
        raise ValidationError("Use an international number with country code, e.g. +919876543210.")
    return compact
