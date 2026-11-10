"""Read-only, tenant-scoped GST return preparation.

No official filing, ITC entitlement, or GST Portal ledger state is inferred
from workshop invoices or purchase bills. Historical posted totals are never
recalculated using today's rate settings.
"""
import calendar
import re
from collections import defaultdict
from datetime import date
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

from rest_framework.exceptions import ValidationError

from apps.invoices.models import Invoice
from apps.inventory.models import SupplierBill
from apps.suppliers.purchase_gst import purchase_bill_components

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z0-9]{13}$")
FINAL_STATUSES = ("Finalized", "Paid", "Issued", "Partially Paid", "Overdue")


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
    rows, issues, included = [], [], []
    for obj in invoices:
        seller = obj.seller_snapshot if isinstance(obj.seller_snapshot, dict) else {}
        seller_gstin = str(seller.get("gstin") or "").strip().upper()
        if not seller_gstin or seller_gstin != str(company.gstin or "").strip().upper():
            issues.append({
                "type": "seller_gstin_unverified", "record": str(obj.id),
                "number": obj.number,
                "message": "Saved seller GSTIN is missing or differs from the selected workshop GSTIN. Excluded pending review.",
            })
            continue
        included.append(obj)
        snapshot = obj.customer_snapshot if isinstance(obj.customer_snapshot, dict) else {}
        gstin = str(snapshot.get("gstin") or snapshot.get("gstIn") or "").strip().upper()
        pos = str(snapshot.get("placeOfSupply") or snapshot.get("stateCode") or snapshot.get("state") or "").strip()
        valid_gstin = not gstin or bool(GSTIN_RE.fullmatch(gstin))
        if not gstin and obj.customer.gstin:
            issues.append({"type": "historical_gstin_unverified", "record": str(obj.id),
                           "number": obj.number,
                           "message": "Customer GSTIN is missing from the saved invoice snapshot. Verify B2B/B2C classification."})
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
            "classification": "B2B" if gstin else ("Review Required" if obj.customer.gstin else "B2C"),
            "taxable": money(obj.taxable), "cgst": money(obj.cgst),
            "sgst": money(obj.sgst), "igst": money(obj.igst),
            "total": money(obj.total), "taxMode": obj.tax_mode,
        }
        rows.append(row)
    return rows, issues, included


def _totals(rows):
    keys = ("taxable", "cgst", "sgst", "igst", "total")
    result = {k: ZERO for k in keys}
    for row in rows:
        for k in keys:
            result[k] += amount(row[k])
    return {k: money(v) for k, v in result.items()}


def inward_rows(company, period=None):
    rows, issues = [], []
    bills = supplier_queryset(company, period) if period else (
        SupplierBill.objects.filter(company=company, status="Posted")
        .select_related("supplier").order_by("-invoice_date", "-created_at")
    )
    for bill in bills:
        items = bill.items if isinstance(bill.items, list) else []
        try:
            components = purchase_bill_components(items, bill.tax)
        except (ValueError, TypeError, InvalidOperation, KeyError):
            components = {
                "taxComponentsVerified": False, "unallocatedLegacyTax": money(bill.tax),
                "cgst": "0.00", "sgst": "0.00", "utgst": "0.00", "igst": "0.00",
            }
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


def unverified_expense_gst(company, period):
    """Surface Finance Expenses that contain GST without inventing a tax credit.

    Expense stores only a gross value and an undifferentiated tax amount. It
    lacks immutable supplier GSTIN, invoice lines and IGST/CGST/SGST heads,
    so these amounts must NEVER enter the eligible ITC or GSTR-3B totals.
    """
    from apps.expenses.models import Expense

    expenses = Expense.objects.filter(
        company=company, date__gte=period["from"], date__lte=period["to"],
        tax__gt=ZERO,
    ).exclude(
        status__iexact="Draft",
    ).exclude(
        status__iexact="Rejected",
    ).exclude(
        status__iexact="Cancelled",
    ).order_by("-date", "-created_at")
    rows = [{
        "id": str(exp.pk), "date": exp.date.isoformat(),
        "category": exp.category, "vendor": exp.vendor,
        "description": exp.description.split("\n", 1)[0][:120],
        "reference": exp.reference, "amount": money(exp.amount),
        "tax": money(exp.tax), "status": exp.status,
        "itcEligibility": "Unverified - excluded from ITC",
    } for exp in expenses]
    return rows


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
                   "B2B" if isinstance(obj.customer_snapshot, dict) and obj.customer_snapshot.get("gstin")
                   else ("Review Required" if obj.customer.gstin else "B2C"))
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


def prepare(company, period, *, include_expense_review=False):
    """Generate provisional GST snapshots, credit notes and approved tax review.

    No official filing or portal electronic credit/cash ledger is inferred.
    Output and inputs preserve original posted documents; returned documents
    are separate signed components posted in the note's date period.
    """
    from .models import (
        GSTR2BImport, GSTInputTaxClaim, GSTInputTaxReversal,
        GSTFilingReview, GSTSalesCreditNote,
    )
    from .itc import credit_totals

    sales, sales_issues, invoices = outward_rows(company, period)
    purchases, purchase_issues = inward_rows(company, period)
    expense_gst_review = (
        unverified_expense_gst(company, period) if include_expense_review else []
    )
    hsn, hsn_issues = hsn_summary(invoices)
    gross_totals = _totals(sales)
    cancelled_credits = GSTSalesCreditNote.objects.filter(
        company=company, invoice__status="Cancelled",
        date__gte=period["from"], date__lte=period["to"],
    )
    notes = list(GSTSalesCreditNote.objects.filter(
        company=company,
        date__gte=period["from"], date__lte=period["to"],
    ).exclude(invoice__status="Cancelled")
     .select_related("invoice").order_by("-date", "-created_at"))
    note_rows = [{
        "id": str(note.pk), "number": note.number, "date": note.date.isoformat(),
        "invoiceId": str(note.invoice_id), "invoiceNumber": note.invoice.number,
        "reason": note.reason,
        "taxable": money(note.taxable), "cgst": money(note.cgst),
        "sgst": money(note.sgst), "igst": money(note.igst),
        "total": money(note.taxable + note.cgst + note.sgst + note.igst),
    } for note in notes]
    notes_totals = _totals(note_rows)
    net_totals = {
        key: money(amount(gross_totals[key]) - amount(notes_totals[key]))
        for key in ("taxable", "cgst", "sgst", "igst", "total")
    }
    output_gst = sum((amount(net_totals[k]) for k in ("cgst", "sgst", "igst")), ZERO)
    purchase_gst = sum((amount(row["tax"]) for row in purchases), ZERO)
    credits = credit_totals(company, period["period"])
    approved = sum(credits["claimed"].values(), ZERO)
    reversed_itc = sum(credits["reversed"].values(), ZERO)
    import_batches = GSTR2BImport.objects.filter(company=company, period=period["period"])
    review = GSTFilingReview.objects.filter(company=company, period=period["period"]).first()
    approved_items = GSTInputTaxClaim.objects.filter(company=company, period=period["period"])
    reversed_items = GSTInputTaxReversal.objects.filter(company=company, period=period["period"])
    issues = sales_issues + purchase_issues + hsn_issues
    if expense_gst_review:
        issues.append({
            "type": "expense_gst_unverified", "record": "",
            "message": (
                f"{len(expense_gst_review)} operational expenses contain recorded GST "
                "but lack verified supplier tax-head documentation. Excluded from ITC."
            ),
        })
    for note in cancelled_credits:
        issues.append({
            "type": "cancelled_original_credit_note", "record": str(note.invoice_id),
            "number": note.number,
            "message": "Credit note source invoice is cancelled; credit was excluded from the net GST preview to prevent double reduction. Review portal amendment.",
        })
    if not str(company.gstin or "").strip():
        issues.append({
            "type": "missing_registration", "record": "",
            "message": "Workshop is not GST registered. Return totals are preparation only.",
        })
    if any(row.get("classification") == "Review Required" for row in sales):
        issues.append({
            "type": "customer_gstin_review", "record": "",
            "message": "B2B/B2C classification includes historical unverified customers.",
        })
    if not import_batches.exists() and purchases:
        issues.append({
            "type": "missing_2b_evidence", "record": "",
            "message": "No official GSTR-2B evidence is saved for this period.",
        })
    warnings = [
        "GSTR-1 amendments, HSN item-wise tax heads and document sequence summary require statutory review.",
        "Sales credit notes in this preparation record do not automatically refund customers or change invoices.",
        "ITC is counted only after an authorized 2B-matched review; statutory ITC restrictions remain the reviewer's responsibility.",
        "GSTR-3B utilization rules, reverse-charge tax, interest, prior-period adjustments and portal cash/credit balances require separate verification.",
        "This workspace cannot submit to or verify status on the official GST Portal.",
    ]
    # This is NOT an official or payable liability calculation.
    provisional_gap = output_gst - approved + reversed_itc
    filing = {
        "status": review.status if review else "Not Reviewed",
        "verified": False, "portalVerified": False,
        "reference": review.reference if review else "",
        "filedOn": review.filed_on.isoformat() if review and review.filed_on else None,
    }
    return {
        "period": period["period"], "financialYear": period["financialYear"],
        "company": {
            "name": company.legal_name or company.name,
            "gstin": company.gstin or "", "registered": bool(company.gstin),
        },
        "overview": {
            **net_totals,
            "grossOutputGST": money(sum((
                amount(gross_totals[k]) for k in ("cgst", "sgst", "igst")
            ), ZERO)),
            "outputGST": money(output_gst),
            "salesCreditTax": money(sum((
                amount(notes_totals[k]) for k in ("cgst", "sgst", "igst")
            ), ZERO)),
            "purchaseGST": money(purchase_gst),
            "unverifiedExpenseGST": money(sum((
                amount(row["tax"]) for row in expense_gst_review
            ), ZERO)) if include_expense_review else None,
            "expenseGSTReviewCount": len(expense_gst_review) if include_expense_review else None,
            "eligibleITC": money(approved),
            "reversedITC": money(reversed_itc),
            "indicativeGapBeforeUtilization": money(provisional_gap),
            "estimatedNetLiability": None,
            "verifiedPurchaseTax": money(sum((
                amount(row["tax"]) for row in purchases if row["taxVerified"]
            ), ZERO)),
            "invoiceCount": len(sales), "purchaseCount": len(purchases),
            "salesCreditNotesCount": len(notes),
            "itcClaimCount": approved_items.count(),
            "itcReversalCount": reversed_items.count(),
            "saved2BImports": import_batches.count(),
        },
        "gstr1": {
            "rows": sales, "totals": gross_totals,
            "creditNotes": note_rows, "creditTotals": notes_totals,
            "netTotals": net_totals,
        },
        "gstr3b": {
            "outwardTaxable": net_totals["taxable"],
            "outputCGST": net_totals["cgst"],
            "outputSGST": net_totals["sgst"],
            "outputIGST": net_totals["igst"],
            "eligibleITC": money(approved), "reversedITC": money(reversed_itc),
            "approvedITCByHead": {key: money(value) for key, value in credits["claimed"].items()},
            "reversedITCByHead": {key: money(value) for key, value in credits["reversed"].items()},
            "reverseCharge": None, "estimatedNetLiability": None,
            "indicativeGapBeforeUtilization": money(provisional_gap),
            "provisional": True,
        },
        "gstr2b": {
            "purchases": purchases, "expenseGSTReview": expense_gst_review,
            "imported": import_batches.exists(),
            "savedImportCount": import_batches.count(),
        },
        "itc": {
            "claimed": [str(v) for v in credits["claimed"].values()],
            "claimedByHead": {key: money(v) for key, v in credits["claimed"].items()},
            "reversedByHead": {key: money(v) for key, v in credits["reversed"].items()},
        },
        "hsn": {"rows": hsn, "provisional": True},
        "issues": issues, "warnings": warnings, "filing": filing,
    }
