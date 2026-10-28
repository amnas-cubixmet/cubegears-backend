"""Validate an India E-Way Bill *local preparation draft*.

No EBN is generated here. The NIC/GST portal is the only authority for official
numbers, generation timestamps, validity, QR and Part-B updates. E-Way Bill
thresholds and special exemptions are case-dependent; never infer requirement
from a service invoice total.
"""
from decimal import Decimal, InvalidOperation
import re
from rest_framework.exceptions import ValidationError
from django.utils import timezone
from apps.jobs.india_gst import STATE_CODES
from .gst_rate_options import find_gst_choice

GSTIN = re.compile(r"^[0-9]{2}[A-Z0-9]{13}$")
STATE = re.compile(r"^[0-9]{2}$")
PIN = re.compile(r"^[1-9][0-9]{5}$")
HSN = re.compile(r"^[0-9]{2,8}$")
NUMBER = re.compile(r"^[A-Za-z0-9/-]{1,50}$")
READONLY_PORTAL_FIELDS = frozenset({
    "ewayBillNo", "number", "generatedAt", "generatedBy", "validUntil",
    "qrCodeDataUrl", "qrCodeBase64", "qrCodeText", "barcodeBase64",
    "barcodeDataUrl", "status", "updatedAt", "createdAt",
})


def strip_official_fields(payload):
    """Client input may never write official NIC-provided EWB evidence."""
    return {key: value for key, value in payload.items()
            if key not in READONLY_PORTAL_FIELDS}



def workshop_supply_party(company, branch=None):
    """Build the OUTWARD dispatch address from server-owned company/branch data.

    Branch address/city/PIN take precedence. No hard-coded state/GSTIN or
    client-provided "from" address is trusted for outward E-Way Bill drafts.
    """
    from apps.jobs.india_gst import state_code
    branch_address = str(getattr(branch, "address", "") or "").strip()
    branch_city = str(getattr(branch, "city", "") or "").strip()
    branch_pin = str(getattr(branch, "pincode", "") or "").strip()
    effective_state = str(getattr(branch, "state", "") or getattr(company, "state", "") or "")
    gstin = str(getattr(company, "gstin", "") or "").strip().upper() or "URP"
    return {
        "gstin": gstin,
        "tradeName": str(getattr(company, "name", "") or getattr(company, "legal_name", "") or "").strip(),
        "address": branch_address or str(getattr(company, "address", "") or "").strip(),
        "place": branch_city or str(getattr(company, "city", "") or "").strip(),
        "pincode": branch_pin or str(getattr(company, "pincode", "") or "").strip(),
        "stateCode": state_code(effective_state),
    }


def validate_tax_geography(payload):
    """Prevent incompatible CGST/SGST + IGST on ordinary domestic supplies.

    Special transaction classes (exports, imports, SEZ etc.) may require
    separate treatment; do not infer such exceptions from user-entered rates.
    """
    if payload.get("subSupplyType") not in {"Supply", "Sales Return"}:
        return
    from_code = str((payload.get("from") or {}).get("stateCode") or "")
    to_code = str((payload.get("to") or {}).get("stateCode") or "")
    if from_code not in STATE_CODES or to_code not in STATE_CODES:
        return
    for index, item in enumerate(payload.get("items") or [], 1):
        if not isinstance(item, dict):
            continue
        cgst = _money(item.get("cgstRate") or "0", "cgstRate")
        sgst = _money(item.get("sgstRate") or "0", "sgstRate")
        igst = _money(item.get("igstRate") or "0", "igstRate")
        if from_code == to_code and igst:
            raise ValidationError({"items": f"Item {index}: intra-state supply must use CGST and SGST, not IGST."})
        if from_code != to_code and (cgst or sgst):
            raise ValidationError({"items": f"Item {index}: inter-state supply must use IGST, not CGST/SGST."})


def validate_party(name, value):
    if not isinstance(value, dict):
        raise ValidationError({name: "Supply party details are required."})
    if not str(value.get("tradeName") or "").strip():
        raise ValidationError({name: "Trade name is required."})
    gstin = str(value.get("gstin") or "").strip().upper()
    if not (GSTIN.fullmatch(gstin) or gstin == "URP"):
        raise ValidationError({name: "Enter valid GSTIN or URP for unregistered party."})
    if gstin != "URP" and gstin[:2] != str(value.get("stateCode") or ""):
        raise ValidationError({name: "GSTIN state code differs from party state."})
    if not str(value.get("address") or "").strip() or not str(value.get("place") or "").strip():
        raise ValidationError({name: "Full address and place are required."})
    if not PIN.fullmatch(str(value.get("pincode") or "")):
        raise ValidationError({name: "Enter a valid six-digit PIN code."})
    if not STATE.fullmatch(str(value.get("stateCode") or "")) or str(value.get("stateCode")) not in STATE_CODES:
        raise ValidationError({name: "State code must be two digits."})


def _money(value, name):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError({name: "Enter a valid nonnegative amount."})
    if not number.is_finite() or number < 0 or number > Decimal("9999999999.99"):
        raise ValidationError({name: "Enter a valid nonnegative amount."})
    if number.as_tuple().exponent < -2:
        raise ValidationError({name: "Amount supports at most two decimal places."})
    return number


def validate_preparation(payload):
    """Validate a local EWB draft before status 'Ready for API'."""
    for key in ("supplyType", "subSupplyType", "transactionType",
                "documentType", "documentNo", "documentDate"):
        if not str(payload.get(key) or "").strip():
            raise ValidationError({key: "Required to prepare E-Way Bill."})
    if not NUMBER.fullmatch(str(payload["documentNo"])):
        raise ValidationError({"documentNo": "Document number must contain only letters, digits, / and -."})

    from datetime import date
    try:
        document_date = date.fromisoformat(str(payload["documentDate"]))
    except (ValueError, TypeError):
        raise ValidationError({"documentDate": "Enter a valid YYYY-MM-DD date."})
    if document_date > timezone.localdate():
        raise ValidationError({"documentDate": "Document date cannot be in the future."})

    validate_party("from", payload.get("from"))
    validate_party("to", payload.get("to"))
    goods = payload.get("items")
    if not isinstance(goods, list) or not 1 <= len(goods) <= 75:
        raise ValidationError({"items": "Enter 1–75 transported goods lines; exclude labour-only lines."})
    for index, row in enumerate(goods, 1):
        if not isinstance(row, dict):
            raise ValidationError({"items": f"Item {index} is invalid."})
        if not str(row.get("productName") or "").strip():
            raise ValidationError({"items": f"Enter goods name for item {index}."})
        if not HSN.fullmatch(str(row.get("hsnCode") or "")):
            raise ValidationError({"items": f"Enter valid goods HSN for item {index}."})
        qty = _money(row.get("qty"), f"items[{index}].qty")
        if qty <= 0:
            raise ValidationError({"items": f"Quantity must be positive for item {index}."})
        if not str(row.get("unit") or "").strip():
            raise ValidationError({"items": f"Unit is required for item {index}."})
        _money(row.get("taxableValue"), f"items[{index}].taxableValue")
        tax_rates = []
        for key in ("cgstRate", "sgstRate", "igstRate", "cessRate"):
            rate = _money(row.get(key) if row.get(key) not in ("", None) else "0", key)
            if rate > 100:
                raise ValidationError({key: "GST rate cannot exceed 100%."})
            tax_rates.append(rate)
        if tax_rates[2] and (tax_rates[0] or tax_rates[1]):
            raise ValidationError({"items": f"Use either IGST or CGST/SGST for item {index}, not both."})
        if tax_rates[0] != tax_rates[1]:
            raise ValidationError({"items": f"CGST and SGST rates must match for item {index}."})
        selected_label = str(row.get("gstChoice") or "").strip()
        if selected_label:
            selection = find_gst_choice(selected_label)
            expected = (tax_rates[0] + tax_rates[1]) if selection and selection["taxMode"] == "cgst_sgst" else tax_rates[2]
            if not selection or Decimal(selection["totalRate"]) != expected:
                raise ValidationError({
                    "items": f"Item {index}: selected GST label does not match the GST rates."
                })
            from_code = str((payload.get("from") or {}).get("stateCode") or "")
            to_code = str((payload.get("to") or {}).get("stateCode") or "")
            if from_code in STATE_CODES and to_code in STATE_CODES:
                mode = "cgst_sgst" if from_code == to_code else "igst"
                if selection["taxMode"] != mode:
                    raise ValidationError({
                        "items": f"Item {index}: selected GST type conflicts with supply states."
                    })

    transport = payload.get("transport") or {}
    if not isinstance(transport, dict):
        raise ValidationError({"transport": "Transport details are invalid."})
    if transport.get("mode") not in ("Road", "Rail", "Air", "Ship"):
        raise ValidationError({"transport": "Choose transport mode."})
    try:
        distance = int(str(transport.get("distanceKm") or "0"))
    except ValueError:
        raise ValidationError({"transport": "Distance must be a whole number."})
    if distance <= 0 or distance > 99999:
        raise ValidationError({"transport": "Enter approximate distance in whole kilometres."})
    if transport["mode"] == "Road":
        if not (str(transport.get("vehicleNo") or "").strip()
                or str(transport.get("transporterId") or "").strip()):
            raise ValidationError({"transport": "Enter a transport vehicle or transporter ID."})
    elif not str(transport.get("transportDocNo") or "").strip():
        raise ValidationError({"transport": "Transport document number is required for non-road movement."})
    _money(payload.get("otherAmount") or "0", "otherAmount")
    _money(payload.get("cessNonAdvolAmount") or "0", "cessNonAdvolAmount")
    validate_tax_geography(payload)
