"""Strict international phone normalization for click-to-chat only."""
import re

# Existing company-panel country selector: India and GCC destinations.
_COUNTRY_LENGTHS = {"91": 10, "971": 9, "966": 9, "974": 8,
                    "968": 8, "965": 8, "973": 8}


def normalize_recipient(value, *, country=""):
    raw = str(value or "").strip()
    if not raw:
        return ""
    digits = re.sub(r"\D", "", raw)
    # Older Indian workshop records sometimes stored a local 10-digit mobile.
    # Infer +91 ONLY when the owning workshop is explicitly in India.
    # Never infer an international destination from an unscoped number.
    if str(country or "").strip().lower() in {"india", "in"} and re.fullmatch(r"[6-9]\d{9}", digits):
        return "91" + digits
    if not raw.startswith("+") and not any(
        digits.startswith(code) for code in _COUNTRY_LENGTHS
    ):
        return ""
    for country_code, length in _COUNTRY_LENGTHS.items():
        if digits.startswith(country_code) and len(digits) == len(country_code) + length:
            return digits
    return ""
