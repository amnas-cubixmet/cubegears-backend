"""Indian GST estimate calculation.

Tax treatment depends on the workshop's GST registration, the actual place of
supply and the classified HSN/SAC for *each* line. No universal rate applies
to arbitrary consumables or custom services.

This module uses tax-exclusive prices and invoice-time discounts. Values are
calculated using decimal paise rounding (ROUND_HALF_UP).
"""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
from rest_framework.exceptions import ValidationError

# Requested dropdown presets, NOT a substitute for HSN/SAC classification.
# The API continues to accept valid historical/legally applicable percentages.
GST_CGST_SGST_COMPONENT_PRESETS = ("2.5", "5", "9", "18")
GST_IGST_PRESETS = ("5",)  # repeated 5% entry consolidated

def gst_dropdown_presets():
    """Read-only API metadata: component rates stay distinct from total rate."""
    return {
        "cgst": list(GST_CGST_SGST_COMPONENT_PRESETS),
        "sgst": list(GST_CGST_SGST_COMPONENT_PRESETS),
        "igst": list(GST_IGST_PRESETS),
        "intraTotal": [str(int(Decimal(value) * 2)) for value in GST_CGST_SGST_COMPONENT_PRESETS],
    }

CENT = Decimal("0.01")
STATE_NAMES = {
    "jammu and kashmir": "01", "himachal pradesh": "02", "punjab": "03",
    "chandigarh": "04", "uttarakhand": "05", "haryana": "06", "delhi": "07",
    "rajasthan": "08", "uttar pradesh": "09", "bihar": "10", "sikkim": "11",
    "arunachal pradesh": "12", "nagaland": "13", "manipur": "14",
    "mizoram": "15", "tripura": "16", "meghalaya": "17", "assam": "18",
    "west bengal": "19", "jharkhand": "20", "odisha": "21", "chhattisgarh": "22",
    "madhya pradesh": "23", "gujarat": "24", "dadra and nagar haveli and daman and diu": "26",
    "maharashtra": "27", "andhra pradesh": "37", "karnataka": "29", "goa": "30",
    "lakshadweep": "31", "kerala": "32", "tamil nadu": "33",
    "puducherry": "34", "andaman and nicobar islands": "35",
    "telangana": "36", "ladakh": "38",
}
STATE_CODES = set(STATE_NAMES.values())


def state_code(value):
    label = str(value or "").strip()
    match = re.match(r"^(\d{2})(?:\b|[-\s])", label)
    if match and match.group(1) in STATE_CODES:
        return match.group(1)
    return STATE_NAMES.get(label.lower(), "")



def registration_state_diagnostics(*, company, branch=None):
    """Describe the effective supplying state's mismatch without weakening GST checks.

    A blank branch.state inherits the company state, consistent with invoice
    calculation. The company GSTIN remains the only registered seller identity.
    """
    gstin = str(getattr(company, "gstin", "") or "").strip().upper()
    supplier_code = gstin[:2] if re.fullmatch(r"\d{2}[A-Z0-9]{13}", gstin) and gstin[:2] in STATE_CODES else ""
    branch_value = str(getattr(branch, "state", "") or "").strip()
    company_value = str(getattr(company, "state", "") or "").strip()
    effective = branch_value or company_value
    effective_code = state_code(effective)
    return {
        "gstinStateCode": supplier_code,
        "supplyingState": effective,
        "supplyingStateCode": effective_code,
        "stateSource": "branch" if branch_value else "company",
        "registrationStateMismatch": bool(supplier_code and effective_code and supplier_code != effective_code),
    }

def gst_context(*, company, branch, customer, config):
    if not isinstance(config, dict) or config.get("mode") not in {"gst", "non_gst"}:
        raise ValidationError({"gst": "Choose a valid GST billing mode."})
    if config["mode"] == "non_gst":
        if (company.gstin or "").strip():
            raise ValidationError({"gst": "A GST-registered workshop must classify each supply (use 0% only for eligible exempt/nil-rated lines)."})
        return {"mode": "non_gst", "taxMode": "none", "supplierState": "", "placeOfSupply": ""}
    gstin = str(company.gstin or "").strip().upper()
    if not re.fullmatch(r"\d{2}[A-Z0-9]{13}", gstin) or gstin[:2] not in STATE_CODES:
        raise ValidationError({"gst": "Configure a valid 15-character workshop GSTIN in Company Settings."})
    supplier = gstin[:2]
    diagnostics = registration_state_diagnostics(company=company, branch=branch)
    if diagnostics["registrationStateMismatch"]:
        raise ValidationError({"gst": "Workshop branch state differs from company GSTIN. Configure the supplying GST registration."})
    pos = state_code(config.get("placeOfSupply") or getattr(customer, "state", ""))
    if not pos:
        raise ValidationError({"gst": "Select the legal place-of-supply state (not the vehicle number)."})
    return {
        "mode": "gst",
        "taxMode": "cgst_sgst" if supplier == pos else "igst",
        "supplierState": supplier,
        "placeOfSupply": pos,
    }


def gst_rate(value):
    try:
        rate = Decimal(str(value))
    except (ValueError, TypeError, InvalidOperation):
        raise ValidationError({"taxRate": "Enter a valid GST percentage."})
    if (not rate.is_finite() or not Decimal("0") <= rate <= Decimal("100")
            or rate.as_tuple().exponent < -2):
        raise ValidationError({"taxRate": "GST rate must be between 0 and 100 with at most 2 decimals."})
    return rate


def calculate_tax(lines, discount, tax_mode):
    """Distribute document discount proportionally; return per-item & document GST totals."""
    if tax_mode not in {"none", "cgst_sgst", "igst"}:
        raise ValidationError({"gst": "Unsupported GST tax mode."})
    discount = Decimal(str(discount)).quantize(CENT, rounding=ROUND_HALF_UP)
    base = sum((Decimal(str(row["total"])) for row in lines), Decimal("0"))
    if discount < 0 or discount > base:
        raise ValidationError({"discount": "Discount cannot exceed taxable subtotal."})
    allocated = Decimal("0")
    cumulative_gross = Decimal("0")
    result = []
    total_cgst = total_sgst = total_igst = total_taxable = Decimal("0")
    for index, row in enumerate(lines):
        gross = Decimal(str(row["total"]))
        cumulative_gross += gross
        # Cumulative allocation ensures paise-level discounts work even when
        # there are many small items: no unpaid discount gets dumped on the last.
        target = (discount * cumulative_gross / base).quantize(
            CENT, rounding=ROUND_HALF_UP
        ) if base else Decimal("0")
        if index == len(lines) - 1:
            target = discount
        allocation = target - allocated
        if allocation < 0 or allocation > gross:
            raise ValidationError({"discount": "Discount allocation is inconsistent."})
        allocated = target
        taxable = gross - allocation
        rate = gst_rate(row.get("taxRate", "0")) if tax_mode != "none" else Decimal("0")
        if tax_mode == "cgst_sgst":
            cgst = (taxable * rate / 200).quantize(CENT, rounding=ROUND_HALF_UP)
            sgst = (taxable * rate / 200).quantize(CENT, rounding=ROUND_HALF_UP)
            igst = Decimal("0")
        elif tax_mode == "igst":
            cgst = sgst = Decimal("0")
            igst = (taxable * rate / 100).quantize(CENT, rounding=ROUND_HALF_UP)
        else:
            cgst = sgst = igst = Decimal("0")
        total_taxable += taxable
        total_cgst += cgst
        total_sgst += sgst
        total_igst += igst
        result.append({
            **row, "taxRate": str(rate), "discountAllocation": str(allocation),
            "taxable": str(taxable), "cgst": str(cgst), "sgst": str(sgst),
            "igst": str(igst), "tax": str(cgst + sgst + igst),
        })
    if allocated != discount:
        raise ValidationError({"discount": "Unable to allocate discount to estimate lines."})
    taxes = total_cgst + total_sgst + total_igst
    return {
        "lines": result, "taxable": total_taxable, "tax": taxes,
        "cgst": total_cgst, "sgst": total_sgst, "igst": total_igst,
        "total": total_taxable + taxes,
    }
