import re
from uuid import UUID


UUID_PATTERN = r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
REFERENCE_PATTERN = r"[Rr][Ee][Ff]-[0-9]{1,39}"


def record_reference(value):
    """Stable reference for an existing UUID, including historical records."""
    text = str(value or "")
    return f"ref-{UUID(text).int}" if re.fullmatch(UUID_PATTERN, text) else text


def record_label(value):
    return re.sub(UUID_PATTERN, lambda match: record_reference(match[0]), str(value or ""))


def record_uuid(value):
    text = str(value)
    if re.fullmatch(REFERENCE_PATTERN, text):
        return UUID(int=int(text[4:]))
    if re.fullmatch(UUID_PATTERN, text):
        return UUID(text)
    raise ValueError("Invalid record reference.")


class RecordReferenceConverter:
    regex = f"(?:{REFERENCE_PATTERN}|{UUID_PATTERN})"

    def to_python(self, value):
        return record_uuid(value)

    def to_url(self, value):
        return record_reference(record_uuid(value))
