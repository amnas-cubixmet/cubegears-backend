"""Explicit GST-component selection for new supplier purchase lines.

This opt-in pathway never adds a missing CGST, SGST, UTGST or IGST
component. Legacy combined-rate purchase lines remain readable.
All amounts are Decimal and all statutory posting checks are server-side.
"""
import re
from decimal import Decimal, ROUND_HALF_UP

from rest_framework.exceptions import ValidationError

CENT = Decimal("0.01")
LOCAL_RATES = frozenset({"2.5", "5", "9", "18"})
IGST_RATES = frozenset({"5", "18"})
ZERO_TREATMENTS = frozenset({"exempt", "nil_rated", "zero_rated", "non_gst"})
UTGST_CODES = frozenset({"04", "26", "31", "35", "38"})


def money_tax(value):
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def manual_purchase_components(subtotal, selections, context, treatment="", zero_rating_basis=""):
    """Calculate only what the user explicitly selected, even for incomplete drafts.

    A valid selection is needed before a supplier bill can be posted. Selection
    metadata is persisted on the line's existing JSON field (no schema rewrite).
    """
    if not isinstance(selections, list) or len(selections) > 2:
        raise ValidationError({"taxComponents": "Select at most two distinct GST components."})
    if any(not isinstance(value, str) for value in selections):
        raise ValidationError({"taxComponents": "Each GST component must be a selection code."})
    if len(selections) != len(set(selections)):
        raise ValidationError({"taxComponents": "Cannot select the same GST component twice."})

    mode = context.get("taxMode", "legacy")
    zero = Decimal("0.00")
    amounts = {"cgst": zero, "sgst": zero, "utgst": zero, "igst": zero}
    rates = {}
    total_rate = Decimal("0")
    message = ""
    status = "incomplete"
    treatment = str(treatment or "").strip()
    basis = str(zero_rating_basis or "").strip().lower()

    if selections == ["zero"]:
        if mode == "unregistered":
            treatment = "unregistered"
            status = "valid"
        elif treatment not in ZERO_TREATMENTS:
            message = "Classify zero tax as Exempt, Nil-rated, Zero-rated or Non-GST."
        elif mode not in ("cgst_sgst", "igst"):
            message = "Verify the supplier GSTIN and Place of Supply before posting."
        elif treatment == "zero_rated" and basis not in ("export", "sez"):
            message = "Select Export or SEZ as the zero-rating basis for statutory review."
        else:
            status = "valid"
    elif "zero" in selections:
        raise ValidationError({"taxComponents": "0% classification cannot be combined with a taxable component."})
    else:
        for entry in selections:
            match = re.fullmatch(r"(cgst|sgst|utgst|igst):(\d+(?:\.\d+)?)", entry)
            if not match:
                raise ValidationError({"taxComponents": "Select a configured GST component and rate."})
            component, raw_rate = match.groups()
            if component in rates:
                raise ValidationError({"taxComponents": "A GST component may be selected only once per line."})
            value = Decimal(raw_rate)
            valid_rates = IGST_RATES if component == "igst" else LOCAL_RATES
            if raw_rate not in valid_rates:
                raise ValidationError({"taxComponents": "This GST rate is not in the configured component menu."})
            rates[component] = value
            total_rate += value
            amounts[component] = money_tax(subtotal * value / 100)

        if not selections:
            message = "Choose the required GST components or 0% classification."
        elif treatment and treatment != "taxable":
            message = "Positive GST components require taxable treatment."
            status = "invalid"
        elif mode in ("unregistered", "legacy"):
            message = "Verify a registered supplier GSTIN and Place of Supply to charge GST."
            status = "invalid"
        elif "igst" in rates:
            if len(rates) == 1 and mode == "igst":
                status = "valid"
            else:
                message = "IGST must be the only component on an inter-state taxable line."
                status = "invalid"
        elif mode == "igst":
            message = "Inter-state taxable purchases require IGST, not CGST/SGST."
            status = "invalid"
        else:
            state_component = "utgst" if context.get("placeOfSupply") in UTGST_CODES else "sgst"
            other_component = "sgst" if state_component == "utgst" else "utgst"
            if other_component in rates:
                message = "Use the correct SGST or UTGST component for the selected jurisdiction."
                status = "invalid"
            elif "cgst" not in rates or state_component not in rates:
                message = "Select both CGST and the matching SGST/UTGST manually before posting."
            elif rates["cgst"] != rates[state_component]:
                message = "CGST and SGST/UTGST must have equal component rates."
                status = "invalid"
            elif total_rate not in (Decimal("5"), Decimal("18")):
                message = (
                    "This combined rate is not a verified standard 5% or 18% GST rate. "
                    "Confirm HSN/SAC and configure a legally applicable rate before posting."
                )
                status = "invalid"
            else:
                status = "valid"
        if status == "valid":
            treatment = "taxable"

    return {
        "tax": sum(amounts.values(), zero),
        **amounts,
        "gstTreatment": treatment,
        "gstRate": str(total_rate),
        "taxComponents": list(selections),
        "taxValidationStatus": status,
        "taxValidationMessage": message,
        "zeroRatingBasis": basis if treatment == "zero_rated" else "",
    }


def validate_manual_purchase_lines(items):
    """Enforce canonical component amounts before posting a statutory bill."""
    for index, row in enumerate(items):
        if "taxComponents" not in row:
            # Legacy combined-rate purchases retain their old validated snapshot.
            continue
        context = {
            "taxMode": row.get("taxMode"),
            "placeOfSupply": row.get("placeOfSupply"),
        }
        try:
            calculated = manual_purchase_components(
                Decimal(str(row["subtotal"])), row["taxComponents"], context,
                row.get("gstTreatment", ""), row.get("zeroRatingBasis", ""),
            )
        except (ValueError, KeyError, TypeError):
            raise ValidationError({"items": f"Line {index + 1}: corrupt GST component metadata."})
        if calculated["taxValidationStatus"] != "valid":
            raise ValidationError({
                "taxComponents": f"Line {index + 1}: {calculated['taxValidationMessage']}"
            })
        for key in ("tax", "cgst", "sgst", "utgst", "igst"):
            try:
                stored = Decimal(str(row[key]))
            except (ValueError, TypeError, KeyError):
                raise ValidationError({"items": f"Line {index + 1}: missing {key} tax amount."})
            if stored != calculated[key]:
                raise ValidationError({
                    "items": f"Line {index + 1}: stored {key} differs from selected GST components."
                })
