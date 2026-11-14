"""National digit limits for workshop Customer/Supplier contact phones.

Accepted format is E.164 for supported country codes. Legacy 10-digit Indian
numbers are normalized to +91 on create/update, without changing old records
during unrelated reads.
"""
import re
from rest_framework.exceptions import ValidationError

COUNTRY_PHONE_LENGTHS = {
    "+91": 10, "+971": 9, "+966": 9, "+974": 8, "+965": 8,
    "+968": 8, "+973": 8, "+1": 10, "+44": 10, "+61": 9,
}


def normalize_contact_phone(value, *, required=False):
    if not isinstance(value, str):
        raise ValidationError("Enter a valid phone number.")
    raw = re.sub(r"[\s().-]", "", value.strip())
    if not raw:
        if required:
            raise ValidationError("Phone number is required.")
        return ""
    if raw.startswith("00"):
        raw = "+" + raw[2:]
    if re.fullmatch(r"\d{10}", raw):
        raw = "+91" + raw
    if not re.fullmatch(r"\+[1-9]\d{7,14}", raw):
        raise ValidationError("Enter a phone number with a valid country code.")
    country = next(
        (code for code in sorted(COUNTRY_PHONE_LENGTHS, key=len, reverse=True)
         if raw.startswith(code)), None
    )
    if not country or len(raw[len(country):]) != COUNTRY_PHONE_LENGTHS[country]:
        raise ValidationError("Use the correct number of digits for this country code.")
    return raw
