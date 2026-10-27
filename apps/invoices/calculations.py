"""Authoritative invoice/estimate money calculation shared by preview and save.

GST is a percentage of each line's *taxable* Qty × Unit Price, not a flat
charge, and client-submitted totals/tax modes are never trusted.
"""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from rest_framework.exceptions import ValidationError
from apps.jobs.india_gst import calculate_tax, gst_context

CENT = Decimal("0.01")


def _amount(value, field, *, default="0"):
    try:
        amount = Decimal(str(value if value not in ("", None) else default))
    except (TypeError, ValueError, InvalidOperation):
        raise ValidationError({field: "Enter a valid amount."})
    if not amount.is_finite() or amount.as_tuple().exponent < -2:
        raise ValidationError({field: "Amount must have at most two decimal places."})
    return amount


def calculate_invoice_amounts(*, company, branch=None, invoice_type="regular",
                              place_of_supply="", items=(), discount=0,
                              adjustment=0, paid=0):
    """Calculate Decimal amounts for both drafts and saved invoices.

    Input item keys use frontend names: qty, rate, discount and taxRate.
    CGST+SGST uses a single *combined* line rate (18 => 9+9).
    """
    registered = bool(str(getattr(company, "gstin", "") or "").strip())
    if invoice_type != ("gst" if registered else "regular"):
        raise ValidationError({"invoiceType": "Invoice type must match workshop GST registration."})
    if registered:
        context = gst_context(
            company=company, branch=branch, customer=None,
            config={"mode": "gst", "placeOfSupply": place_of_supply},
        )
        mode = context["taxMode"]
    else:
        mode = "none"

    raw_lines = []
    subtotal = Decimal("0")
    for index, item in enumerate(items):
        quantity = _amount(item.get("qty"), f"items.{index}.qty")
        unit_price = _amount(item.get("rate"), f"items.{index}.rate")
        line_discount = _amount(item.get("discount"), f"items.{index}.discount")
        if quantity <= 0 or unit_price < 0:
            raise ValidationError({"items": "Quantity must be positive and unit price cannot be negative."})
        gross = (quantity * unit_price).quantize(CENT, rounding=ROUND_HALF_UP)
        if line_discount < 0 or line_discount > gross:
            raise ValidationError({"items": "Item discount cannot exceed Qty × Unit Price."})
        raw = gross - line_discount
        subtotal += raw
        raw_lines.append({"total": str(raw), "taxRate": item.get("taxRate", 0)})
    invoice_discount = _amount(discount, "discount")
    adjustment_amount = _amount(adjustment, "adjustment")
    received = _amount(paid, "paid")
    if received < 0:
        raise ValidationError({"paid": "Received amount cannot be negative."})

    result = calculate_tax(raw_lines, invoice_discount, mode)
    grand_total = (result["total"] + adjustment_amount).quantize(CENT, rounding=ROUND_HALF_UP)
    if grand_total < 0:
        raise ValidationError({"adjustment": "Adjustment cannot make invoice total negative."})
    balance = max(Decimal("0"), grand_total - received)
    line_totals = [
        {
            "taxable": line["taxable"],
            "cgst": line["cgst"],
            "sgst": line["sgst"],
            "igst": line["igst"],
            "gst": line["tax"],
            "total": Decimal(line["taxable"]) + Decimal(line["tax"]),
        }
        for line in result["lines"]
    ]
    return {
        "itemSubtotal": subtotal,
        "documentDiscount": invoice_discount,
        "taxable": result["taxable"],
        "cgst": result["cgst"],
        "sgst": result["sgst"],
        "igst": result["igst"],
        "taxMode": mode,
        "tax": result["tax"],
        "adjustment": adjustment_amount,
        "total": grand_total,
        "paid": received,
        "balance": balance,
        "lineTotals": line_totals,
    }


def persisted_invoice_amounts(invoice):
    """Calculate line display amounts for persisted invoices with saved inputs."""
    lines = [
        {"qty": item.quantity, "rate": item.rate, "discount": item.discount,
         "taxRate": item.tax_rate}
        for item in invoice.items.all()
    ]
    customer = invoice.customer_snapshot or {}
    return calculate_invoice_amounts(
        company=invoice.company, branch=invoice.branch, invoice_type=invoice.invoice_type,
        place_of_supply=customer.get("placeOfSupply") or customer.get("stateCode") or
                        invoice.customer.state,
        items=lines, discount=invoice.discount,
        adjustment=invoice.adjustment, paid=invoice.paid,
    )
