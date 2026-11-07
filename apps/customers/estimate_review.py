"""Public, itemized estimate projection and exact-review checksum.

Only customer-visible quote fields are allowlisted. Never expose the raw
pricing snapshot, catalog IDs, internal notes or idempotency fingerprints.
This projection is re-derived from the immutable saved estimate on every read.
"""
import hashlib
import json
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

CENT = Decimal("0.01")
MAX_TOTAL = Decimal("9999999999.99")
VISIBLE_TYPES = {"Part", "Labour", "Service", "Other"}


def _money(value):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("Invalid monetary amount.") from None
    if not amount.is_finite() or amount < 0 or amount > MAX_TOTAL:
        raise ValueError("Monetary amount out of range.")
    if amount != amount.quantize(CENT):
        raise ValueError("More than two decimal places.")
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def public_estimate(estimate):
    """Only reliable saved line amounts may be offered for customer consent.

    Legacy/inconsistent quotes remain visible with their grand total but are
    not actionable until staff saves a valid replacement quotation.
    """
    base = {
        "id": str(estimate.pk), "jobId": str(estimate.job_id),
        "version": estimate.version, "status": estimate.status,
        "total": str(estimate.total), "reviewable": False,
        "lines": [], "subtotal": str(estimate.subtotal),
        "discount": "0.00", "tax": str(estimate.tax),
        "reviewHash": None,
    }
    raw = estimate.items
    if not isinstance(raw, list) or not 1 <= len(raw) <= 76:
        return base
    pricing = {}
    rows = raw
    if isinstance(raw[-1], dict) and isinstance(raw[-1].get("_pricing"), dict):
        pricing = raw[-1]["_pricing"]
        rows = raw[:-1]
    if not 1 <= len(rows) <= 75:
        return base

    try:
        subtotal = _money(estimate.subtotal)
        tax = _money(estimate.tax)
        total = _money(estimate.total)
        discount = _money(pricing.get("discount", 0))
        result, computed_subtotal = [], Decimal("0.00")
        for row in rows:
            if not isinstance(row, dict) or "_pricing" in row:
                raise ValueError("Invalid quote item.")
            label = row.get("description")
            if not isinstance(label, str) or not 1 <= len(label.strip()) <= 250:
                raise ValueError("Quote item description missing.")
            item_type = row.get("type", "Service")
            if item_type not in VISIBLE_TYPES:
                raise ValueError("Invalid quote item type.")
            quantity = _money(row.get("quantity", 1))
            price = _money(row.get("unitPrice"))
            if not 0 < quantity <= 10000 or price > 10000000:
                raise ValueError("Quote item amount out of range.")
            line_total = (quantity * price).quantize(CENT, rounding=ROUND_HALF_UP)
            if "total" in row and _money(row["total"]) != line_total:
                raise ValueError("Quote line total does not match.")
            computed_subtotal += line_total
            safe = {
                "description": label.strip(), "type": item_type,
                "quantity": str(quantity), "unitPrice": str(price),
                "total": str(line_total),
            }
            if row.get("hsnSac"):
                hsn = str(row["hsnSac"])
                if len(hsn) <= 20 and hsn.isdigit():
                    safe["hsnSac"] = hsn
            if row.get("taxRate") is not None:
                try:
                    rate = Decimal(str(row["taxRate"]))
                    if rate.is_finite() and 0 <= rate <= 100 and rate.as_tuple().exponent >= -2:
                        safe["taxRate"] = str(rate)
                except (InvalidOperation, ValueError, TypeError):
                    pass
            result.append(safe)
        if computed_subtotal != subtotal or subtotal - discount + tax != total:
            raise ValueError("Quote summary does not reconcile.")

        gst = pricing.get("gst")
        tax_breakdown = None
        if isinstance(gst, dict) and gst.get("mode") == "gst":
            cgst, sgst, igst = (_money(gst.get(key, "0")) for key in ("cgst", "sgst", "igst"))
            if cgst + sgst + igst != tax:
                raise ValueError("GST breakdown does not reconcile.")
            tax_breakdown = {
                "cgst": str(cgst), "sgst": str(sgst), "igst": str(igst),
            }
        base.update({
            "reviewable": True, "lines": result, "subtotal": str(subtotal),
            "discount": str(discount), "tax": str(tax),
        })
        if tax_breakdown is not None:
            base["gst"] = tax_breakdown
        # One exact digest for the quote that the customer reviews and responds to.
        # Not a secret or an electronic signature.
        snapshot = {k: base[k] for k in ("id", "jobId", "version", "subtotal", "discount", "tax", "total", "lines")}
        if tax_breakdown is not None:
            snapshot["gst"] = tax_breakdown
        base["reviewHash"] = hashlib.sha256(
            json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
        return base
    except (InvalidOperation, ValueError, TypeError, ArithmeticError):
        return base
