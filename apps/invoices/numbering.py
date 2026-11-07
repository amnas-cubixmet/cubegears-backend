"""Atomic, tenant-scoped invoice/estimate sequence allocation.

The Company row must be locked by the caller for the entire creation transaction.
Cancelled documents are retained, so their identifiers are never reissued.
"""
import re
from django.utils import timezone

from .models import Invoice


def next_document_number(company, kind):
    prefix = "EST" if kind == "estimate" else "INV"
    year = timezone.localdate().year
    stem = f"{prefix}-{year}-"
    pattern = re.compile(r"^" + re.escape(stem) + r"([0-9]+)$")
    sequence = 0
    for number in Invoice.objects.filter(
        company=company, number__startswith=stem,
    ).values_list("number", flat=True):
        match = pattern.fullmatch(number or "")
        if match:
            sequence = max(sequence, int(match.group(1)))
    return f"{stem}{sequence + 1:04d}"
