"""Read-only, tenant-scoped GST return preparation.

No official filing, ITC entitlement, or GST Portal ledger state is inferred
from workshop invoices or purchase bills. Historical posted totals are never
recalculated using today's rate settings.
"""
import calendar
import re
from collections import defaultdict
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from rest_framework.exceptions import ValidationError

from apps.invoices.models import Invoice
from apps.inventory.models import SupplierBill
from apps.suppliers.purchase_gst import purchase_bill_components

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z0-9]{13}$")
FINAL_STATUSES = ("Finalized", "Paid", "Issued")


def money(value):
    return str((Decimal(str(value or 0))).quantize(CENT, rounding=ROUND_HALF_UP))


def amount(value):
    try:
        result = Decimal(str(value or "0"))
    except (ValueError, TypeError, ArithmeticError):
        raise ValidationError({"file": "GST document contains an invalid amount."})
    if not result.is_finite() or abs(result) > 99999999999999:
        raise ValidationError({"file": "GST document contains an invalid amount."})
    return result


def parse_period(raw):
    if raw in (None, ""):
        from django.utils import timezone
        raw = timezone.localdate().strftime("%Y-%m")
    if not re.fullmatch(r"20[0-9]{2}-(0[1-9]|1[0-2])", str(raw)):
        raise ValidationError({"period": "Select a valid return month in YYYY-MM format."})
    year, month = map(int, raw.split("-"))
    start = date(year, month, 1)
    end = date(year, month, calendar.monthrange(year, month)[1])
    fy = year if month >= 4 else year - 1
    return {"period": raw, "from": start, "to": end,
            "financialYear": f"{fy}-{str((fy + 1) % 100).zfill(2)}"}


def invoice_queryset(company, period):
    # Finalized snapshot only. Payment status alone never determines inclusion.
    return (Invoice.objects.filter(
        company=company, kind="invoice", invoice_type="gst",
        date__gte=period["from"], date__lte=period["to"],
        finalized_at__isnull=False, status__in=FINAL_STATUSES,
    ).select_related("customer").prefetch_related("items").order_by("-date", "-created_at"))


def supplier_queryset(company, period):
    return (SupplierBill.objects.filter(
        company=company, status="Posted",
        invoice_date__gte=period["from"], invoice_date__lte=period["to"],
    ).select_related("supplier").order_by("-invoice_date", "-created_at"))


def outward_rows(company, period):
    invoices = list(invoice_queryset(company, period))
    rows, issues = [], []
    for obj in invoices:
        snapshot = obj.customer_snapshot if isinstance(obj.customer_snapshot, dict) else {}
        gstin = str(snapshot.get("gstin") or snapshot.get("gstIn") or obj.customer.gstin or "").strip().upper()
        pos = str(snapshot.get("placeOfSupply") or snapshot.get("stateCode") or snapshot.get("state") or "").strip()
        valid_gstin = not gstin or bool(GSTIN_RE.fullmatch(gstin))
        if not valid_gstin:
            issues.append({"type": "invalid_customer_gstin", "record": str(obj.id),
                           "number": obj.number, "message": "Verify customer GSTIN on source invoice."})
        if not pos:
            issues.append({"type": "missing_place_of_supply", "record": str(obj.id),
                           "number": obj.number, "message": "Verify the original invoice place of supply."})
        if any(not str(line.hsn_sac or "").strip() for line in obj.items.all()):
            issues.append({"type": "missing_hsn", "record": str(obj.id),
                           "number": obj.number, "message": "Some invoice lines have no HSN/SAC."})
        if obj.tax_mode not in {"cgst_sgst", "igst", "none"}:
            issues.append({"type": "tax_mode", "record": str(obj.id),
                           "number": obj.number, "message": "Invoice tax jurisdiction needs review."})
        row = {
            "id": str(obj.id), "number": obj.number, "date": obj.date.isoformat(),
            "customer": str(snapshot.get("name") or obj.customer.name),
            "customerGSTIN": gstin, "placeOfSupply": pos,
            "classification": "B2B" if gstin else "B2C",
            "taxable": money(obj.taxable), "cgst": money(obj.cgst),
            "sgst": money(obj.sgst), "igst": money(obj.igst),
            "total": money(obj.total), "taxMode": obj.tax_mode,
        }
        rows.append(row)
    return rows, issues, invoices


def _totals(rows):
    keys = ("taxable", "cgst", "sgst", "igst", "total")
    result = {k: ZERO for k in keys}
    for row in rows:
        for k in keys:
            result[k] += amount(row[k])
    return {k: money(v) for k, v in result.items()}


def inward_rows(company, period):
    rows, issues = [], []
    for bill in supplier_queryset(company, period):
        items = bill.items if isinstance(bill.items, list) else []
        components = purchase_bill_components(items, bill.tax)
        verified = components["taxComponentsVerified"] and abs(
            amount(components["unallocatedLegacyTax"])) <= CENT
        if not verified:
            issues.append({"type": "purchase_tax_unverified", "record": str(bill.id),
                           "number": bill.invoice_number,
                           "message": "Purchase bill has unclassified or unverified GST components."})
        rows.append({
            "id": str(bill.id), "supplier": bill.supplier.name,
            "supplierGSTIN": (bill.supplier.gstin or "").strip().upper(),
            "number": bill.invoice_number, "date": bill.invoice_date.isoformat(),
            "taxable": money(bill.subtotal), "tax": money(bill.tax),
            "cgst": components["cgst"] if verified else None,
            "sgst": money(amount(components["sgst"]) + amount(components["utgst"])) if verified else None,
            "igst": components["igst"] if verified else None,
            "taxVerified": verified,
            "itcEligibility": "Requires review",
        })
    return rows, issues


def hsn_summary(invoices):
    # Item taxable amounts are derived previews, not immutable line-tax snapshots.
    # The existing InvoiceItem model stores no tax-head amounts per line.
    buckets = defaultdict(lambda: {"quantity": ZERO, "taxable": ZERO, "count": 0})
    issues = []
    for obj in invoices:
        items = list(obj.items.all())
        bases = [max(ZERO, line.quantity * line.rate - line.discount) for line in items]
        gross = sum(bases, ZERO)
        for line, base in zip(items, bases):
            code = str(line.hsn_sac or "").strip()
            if not code:
                continue
            taxable = base
            if gross > 0 and obj.discount:
                taxable = max(ZERO, base - (obj.discount * base / gross))
            key = (code, line.description, line.unit, str(line.tax_rate),
                   "B2B" if (obj.customer_snapshot or {}).get("gstin") or obj.customer.gstin else "B2C")
            bucket = buckets[key]
            bucket["quantity"] += line.quantity
            bucket["taxable"] += taxable
            bucket["count"] += 1
        if items and gross > 0 and abs(
            sum((max(ZERO, x - obj.discount * x / gross) for x in bases), ZERO) - obj.taxable
        ) > Decimal("0.10"):
            issues.append({"type": "hsn_reconciliation", "record": str(obj.id),
                           "number": obj.number,
                           "message": "Derived line taxable values differ from saved invoice snapshot."})
    rows = [{
        "hsnSac": code, "description": description, "unit": unit, "rate": rate,
        "classification": kind, "quantity": money(v["quantity"]),
        "taxablePreview": money(v["taxable"]), "invoiceLines": v["count"],
        "cgst": None, "sgst": None, "igst": None,
    } for (code, description, unit, rate, kind), v in sorted(buckets.items())]
    return rows, issues


def prepare(company, period):
    sales, sales_issues, invoices = outward_rows(company, period)
    purchases, purchase_issues = inward_rows(company, period)
    hsn, hsn_issues = hsn_summary(invoices)
    totals = _totals(sales)
    output_gst = sum((amount(totals[k]) for k in ("cgst", "sgst", "igst")), ZERO)
    purchase_gst = sum((amount(row["tax"]) for row in purchases), ZERO)
    issues = sales_issues + purchase_issues + hsn_issues
    if not str(company.gstin or "").strip():
        issues.append({"type": "missing_registration", "record": "",
                       "message": "Workshop GSTIN is missing. Select a valid registered entity."})
    # Neither a saved ITC eligibility decision nor reverse-charge ledger exists
    # in the audited source models. The net figure is therefore deliberately null.
    warnings = [
        "GSTR-1 Table 13 document summary, notes and amendments require manual portal review.",
        "HSN line-tax snapshots are unavailable; HSN taxable values are previews only.",
        "Eligible ITC, reversals, reverse charge and cash ledger are not established from purchase tax totals.",
        "This workspace does not submit GST returns or confirm filing.",
    ]
    return {
        "period": period["period"], "financialYear": period["financialYear"],
        "company": {"name": company.legal_name or company.name, "gstin": company.gstin or ""},
        "overview": {
            **totals,
            "outputGST": money(output_gst),
            "purchaseGST": money(purchase_gst),
            "eligibleITC": None, "estimatedNetLiability": None,
            "verifiedPurchaseTax": money(sum((amount(r["tax"]) for r in purchases if r["taxVerified"]), ZERO)),
            "invoiceCount": len(sales), "purchaseCount": len(purchases),
        },
        "gstr1": {"rows": sales, "totals": totals},
        "gstr3b": {
            "outwardTaxable": totals["taxable"], "outputCGST": totals["cgst"],
            "outputSGST": totals["sgst"], "outputIGST": totals["igst"],
            "eligibleITC": None, "reversedITC": None,
            "reverseCharge": None, "estimatedNetLiability": None,
        },
        "gstr2b": {"purchases": purchases, "imported": False},
        "hsn": {"rows": hsn, "provisional": True},
        "issues": issues, "warnings": warnings,
        "filing": {"status": "Not Prepared", "verified": False},
    }
