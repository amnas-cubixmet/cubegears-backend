"""GST outward supply credit note preparation, never a substitute for a refund."""
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import ValidationError

from apps.companies.models import Company
from apps.invoices.models import Invoice
from .models import GSTSalesCreditNote
from .services import parse_period

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def decimal_money(value, field, positive=True):
    try:
        value = Decimal(str(value))
        if (not value.is_finite() or value.as_tuple().exponent < -2
                or value >= Decimal("1000000000000")
                or (value <= ZERO if positive else value < ZERO)):
            raise ValueError
        return value.quantize(CENT)
    except (ValueError, TypeError, ArithmeticError):
        raise ValidationError({field: "Enter a valid amount with at most two decimals."})


def note_info(row):
    return {
        "id": str(row.pk), "invoiceId": str(row.invoice_id),
        "invoiceNumber": row.invoice.number,
        "number": row.number, "date": row.date.isoformat(),
        "reason": row.reason,
        "taxable": str(row.taxable),
        "cgst": str(row.cgst), "sgst": str(row.sgst),
        "igst": str(row.igst),
        "total": str(row.taxable + row.cgst + row.sgst + row.igst),
        "status": "GST Preparation — Customer Refund Separate",
    }


@transaction.atomic
def post_credit_note(request):
    data = request.data
    Company.objects.select_for_update().get(pk=request.user.company_id)
    invoice = get_object_or_404(
        Invoice.objects.select_for_update(), pk=data.get("invoiceId"),
        company_id=request.user.company_id, kind="invoice",
        invoice_type="gst", finalized_at__isnull=False,
    )
    if invoice.status == "Cancelled":
        raise ValidationError({"invoiceId": "Cancelled invoice requires independent GST amendment review."})
    number = str(data.get("number") or "").strip()
    reason = str(data.get("reason") or "").strip()
    key = str(data.get("requestKey") or "").strip()
    if not number or len(number) > 100:
        raise ValidationError({"number": "Credit note number must be 1–100 characters."})
    if not 15 <= len(reason) <= 500:
        raise ValidationError({"reason": "Explain the GST credit note in 15–500 characters."})
    if not 8 <= len(key) <= 120:
        raise ValidationError({"requestKey": "Use an 8–120 character unique request key."})
    date = parse_period(str(data.get("date") or "")[:7])
    from datetime import date as Date
    try:
        note_date = Date.fromisoformat(str(data.get("date") or ""))
    except (ValueError, TypeError):
        raise ValidationError({"date": "Enter a valid YYYY-MM-DD credit note date."})
    if note_date.strftime("%Y-%m") != date["period"]:
        raise ValidationError({"date": "Invalid return period."})
    taxable = decimal_money(data.get("taxable"), "taxable")
    previous = GSTSalesCreditNote.objects.filter(
        company_id=invoice.company_id, request_key=key,
    ).select_related("invoice").first()
    if previous:
        if (previous.invoice_id != invoice.pk or previous.number != number
                or previous.reason != reason or previous.taxable != taxable
                or previous.date != note_date):
            raise ValidationError({"requestKey": "This key refers to a different credit note."})
        return previous, False
    if GSTSalesCreditNote.objects.filter(
        company_id=invoice.company_id, number__iexact=number,
    ).exists():
        raise ValidationError({"number": "Credit note number is already used."})
    if invoice.taxable <= ZERO:
        raise ValidationError({"invoiceId": "Original invoice taxable amount is not positive."})
    past = GSTSalesCreditNote.objects.filter(
        company_id=invoice.company_id, invoice=invoice,
    )
    already = past.aggregate(total=Sum("taxable"))["total"] or ZERO
    if already + taxable > invoice.taxable:
        raise ValidationError({"taxable": "Credit notes exceed the original invoice taxable value."})
    ratio = taxable / invoice.taxable
    taxes = {}
    for head in ("cgst", "sgst", "igst"):
        previous_amount = past.aggregate(total=Sum(head))["total"] or ZERO
        total_tax = Decimal(getattr(invoice, head))
        if already + taxable == invoice.taxable:
            taxes[head] = total_tax - previous_amount
        else:
            taxes[head] = min(
                total_tax - previous_amount,
                (total_tax * ratio).quantize(CENT, rounding=ROUND_HALF_UP),
            )
        if taxes[head] < ZERO:
            raise ValidationError({"invoiceId": "Existing credit-note GST exceeds the original invoice."})
    result = GSTSalesCreditNote.objects.create(
        company_id=invoice.company_id, branch=invoice.branch,
        invoice=invoice, number=number, date=note_date, reason=reason,
        taxable=taxable, request_key=key, created_by=request.user, **taxes,
    )
    return result, True
