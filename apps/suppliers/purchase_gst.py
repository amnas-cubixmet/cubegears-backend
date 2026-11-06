"""Jurisdiction-aware GST components for new supplier purchase lines.

GST rates are entered as combined percentages; component rates are derived.
No historical purchase, stock record or tax classification is modified.
"""
import re
from decimal import Decimal, ROUND_HALF_UP

from rest_framework.exceptions import ValidationError
from apps.jobs.india_gst import state_code, calculate_tax

CENT = Decimal("0.01")
UTGST_CODES = frozenset({"04", "26", "31", "35", "38"})
TREATMENTS = frozenset({"taxable", "exempt", "nil_rated", "zero_rated", "non_gst", "unregistered"})


def purchase_gst_context(supplier, place_of_supply):
    if not place_of_supply:
        return {"taxMode": "legacy", "placeOfSupply": "", "supplierStateCode": ""}
    pos = state_code(str(place_of_supply))
    if not pos:
        raise ValidationError({"placeOfSupply": "Select a valid Indian place-of-supply state."})
    gstin = str(supplier.gstin or "").strip().upper()
    if not gstin:
        return {"taxMode": "unregistered", "placeOfSupply": pos, "supplierStateCode": ""}
    if not re.fullmatch(r"[0-9]{2}[A-Z0-9]{13}", gstin) or not state_code(gstin[:2]):
        raise ValidationError({"supplierId": "Verify the supplier GSTIN before charging GST."})
    return {
        "taxMode": "cgst_sgst" if gstin[:2] == pos else "igst",
        "placeOfSupply": pos,
        "supplierStateCode": gstin[:2],
    }


def purchase_line_components(subtotal, rate, context, treatment):
    mode = context["taxMode"]
    zero = Decimal("0.00")
    if mode == "legacy":
        amount = (subtotal * rate / 100).quantize(CENT, rounding=ROUND_HALF_UP)
        return {"tax": amount, "cgst": zero, "sgst": zero, "utgst": zero, "igst": zero,
                "gstTreatment": "legacy_unclassified"}
    if mode == "unregistered":
        if rate != 0:
            raise ValidationError({"gstRate": "An unregistered supplier cannot charge GST."})
        treatment = "unregistered"
    elif rate > 0:
        if treatment not in ("", "taxable"):
            raise ValidationError({"gstTreatment": "Positive GST requires taxable treatment."})
        treatment = "taxable"
    elif treatment not in TREATMENTS - {"taxable", "unregistered"}:
        raise ValidationError({"gstTreatment": "Classify 0% as exempt, nil-rated, zero-rated or non-GST."})
    if mode == "unregistered":
        cgst = sgst = igst = zero
    else:
        line = calculate_tax([{"total": str(subtotal), "taxRate": str(rate)}], zero, mode)["lines"][0]
        cgst, sgst, igst = (Decimal(line[key]) for key in ("cgst", "sgst", "igst"))
    utgst = sgst if mode == "cgst_sgst" and context["placeOfSupply"] in UTGST_CODES else zero
    if utgst:
        sgst = zero
    return {"tax": cgst + sgst + utgst + igst, "cgst": cgst, "sgst": sgst,
            "utgst": utgst, "igst": igst, "gstTreatment": treatment}


def purchase_bill_components(items, tax_total):
    keys = ("cgst", "sgst", "utgst", "igst")
    totals = {key: sum((Decimal(str(row.get(key, "0"))) for row in items), Decimal("0"))
              for key in keys}
    first = items[0] if items else {}
    verified = bool(items) and all(
        row.get("taxMode") in ("cgst_sgst", "igst", "unregistered")
        and row.get("taxValidationStatus", "valid") == "valid"
        for row in items
    )
    return {
        "taxMode": first.get("taxMode", "legacy") if verified else "legacy",
        "placeOfSupply": first.get("placeOfSupply", "") if verified else "",
        "supplierStateCode": first.get("supplierStateCode", "") if verified else "",
        "taxComponentsVerified": verified,
        **{key: str(value.quantize(CENT)) for key, value in totals.items()},
        "unallocatedLegacyTax": str((tax_total - sum(totals.values())).quantize(CENT)),
    }
