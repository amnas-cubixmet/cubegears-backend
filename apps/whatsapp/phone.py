"""E.164 recipient validation for Cloud API and Click-to-Chat.

Do not guess a country prefix for legacy local numbers.
"""
import re


def normalize_recipient(value):
    raw = str(value or "").strip()
    digits = re.sub(r"\D", "", raw)
    if not raw or (not raw.startswith("+") and len(digits) < 11):
        return ""
    return digits if re.fullmatch(r"[1-9][0-9]{7,14}", digits) else ""
