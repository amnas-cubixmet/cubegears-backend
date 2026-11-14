"""Shared structural GSTIN checks for supplier creation and purchases.

This verifies state/format only, not whether a GSTIN is active on the GST portal.
An empty GSTIN is allowed for an unregistered supplier.
"""
import re

from apps.jobs.india_gst import STATE_CODES


GSTIN_PATTERN = re.compile(
    r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$"
)


def normalized_supplier_gstin(value):
    return str(value or "").strip().upper()


def supplier_gstin_error(value):
    gstin = normalized_supplier_gstin(value)
    if not gstin:
        return ""
    if len(gstin) != 15:
        return "GSTIN must contain exactly 15 characters. Leave it blank for an unregistered supplier."
    if gstin[:2] not in STATE_CODES:
        return "GSTIN must begin with a valid Indian state code (for example, 32 for Kerala)."
    if not GSTIN_PATTERN.fullmatch(gstin):
        return "Enter a valid GSTIN format (state code, PAN, entity code, Z and check character)."
    return ""


def supplier_gstin_state_code(value):
    gstin = normalized_supplier_gstin(value)
    return gstin[:2] if gstin and not supplier_gstin_error(gstin) else ""
