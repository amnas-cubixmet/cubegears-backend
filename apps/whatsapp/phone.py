"""Strict international phone normalization for click-to-chat only."""
import re

# Existing company-panel country selector: India and GCC destinations.
_COUNTRY_LENGTHS = {"91": 10, "971": 9, "966": 9, "974": 8,
                    "968": 8, "965": 8, "973": 8}


def normalize_recipient(value):
    raw = str(value or "").strip()
    if not raw:
        return ""
    digits = re.sub(r"\D", "", raw)
    if not raw.startswith("+") and not any(
        digits.startswith(code) for code in _COUNTRY_LENGTHS
    ):
        return ""
    for country_code, length in _COUNTRY_LENGTHS.items():
        if digits.startswith(country_code) and len(digits) == len(country_code) + length:
            return digits
    return ""
