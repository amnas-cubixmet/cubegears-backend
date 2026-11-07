"""Auditable, conservative ITC from immutable purchase tax and persisted GSTR-2B evidence.

Reconciliation proves document agreement, NOT statutory eligibility. Approval
requires human confirmation and a recorded explanation. Claims never modify
payments, stock, bank accounts or the official GST Portal.
"""
from decimal import Decimal, ROUND_HALF_UP
import re

from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import ValidationError

from apps.companies.models import Company
from apps.inventory.models import SupplierBill, SupplierDebitNote
from apps.suppliers.purchase_gst import purchase_bill_components
from .models import GSTR2BImport, GSTInputTaxClaim, GSTInputTaxReversal

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
HEADS = ("cgst", "sgst", "igst")
GSTIN = re.compile(r"^[0-9]{2}[A-Z0-9]{13}$")


def values(bill):
    """Compute verified original tax heads, including UTGST within the state tax column."""
    try:
        components = purchase_bill_components(bill.items, bill.tax)
    except (ArithmeticError, TypeError, ValueError, KeyError, AttributeError):
        raise ValidationError({"billId": "Purchase GST components are invalid; source correction required."})
    if (not components["taxComponentsVerified"]
            or abs(Decimal(components["unallocatedLegacyTax"])) > CENT):
        raise ValidationError({"billId": "Purchase GST breakdown is unverified; do not claim ITC."})
    return {
        "cgst": Decimal(components["cgst"]),
        "sgst": Decimal(components["sgst"]) + Decimal(components["utgst"]),
        "igst": Decimal(components["igst"]),
    }


def debit_components(note):
    bill = note.bill
    values(bill)  # Reject unverifiable historical line GST data.
    components = {head: ZERO for head in HEADS}
    for returned in note.lines:
        index = int(returned["lineIndex"])
        original = bill.items[index]
        bought = Decimal(str(original["quantity"]))
        returned_qty = Decimal(str(returned["quantity"]))
        if bought <= ZERO or returned_qty <= ZERO or returned_qty > bought:
            raise ValidationError({"lines": "Invalid purchase return quantity for GST."})
        for head in HEADS:
            original_head = Decimal(str(original.get(head, 0)))
            if head == "sgst":
                original_head += Decimal(str(original.get("utgst", 0)))
            if not original_head.is_finite() or original_head < ZERO:
                raise ValidationError({"lines": "Invalid purchase GST snapshot."})
            components[head] += (
                original_head * returned_qty / bought
            ).quantize(CENT, rounding=ROUND_HALF_UP)
    return components


def available_heads(bill):
    source = values(bill)
    for note in SupplierDebitNote.objects.filter(
        company_id=bill.company_id, bill=bill,
    ).select_related("bill"):
        reversed_heads = debit_components(note)
        for head in HEADS:
            source[head] = max(ZERO, source[head] - reversed_heads[head])
    return source


@transaction.atomic
def approve_credit(request):
    key_period = str(request.data.get("period") or "")
    from .services import parse_period
    parse_period(key_period)
    company = Company.objects.select_for_update().get(pk=request.user.company_id)
    company_gstin = str(company.gstin or "").strip().upper()
    if not GSTIN.fullmatch(company_gstin):
        raise ValidationError({"company": "Verify the workshop GST registration first."})
    batch = get_object_or_404(
        GSTR2BImport, pk=request.data.get("importId"),
        company=company, period=key_period,
    )
    bill = get_object_or_404(
        SupplierBill.objects.select_for_update().select_related("supplier"),
        pk=request.data.get("billId"), company=company, status="Posted",
    )
    old = GSTInputTaxClaim.objects.filter(company=company, bill=bill).first()
    if old:
        raise ValidationError({
            "billId": "ITC already approved for this purchase bill. Duplicate claims are blocked.",
        })
    supplier_gstin = str(bill.supplier.gstin or "").strip().upper()
    if not GSTIN.fullmatch(supplier_gstin):
        raise ValidationError({"supplier": "Supplier GSTIN must be verified before approval."})
    found = [
        row for row in batch.rows
        if row.get("status") == "Matched"
        and row.get("bookId") == str(bill.pk)
        and row.get("supplierGSTIN") == supplier_gstin
        and str(row.get("itcAvailableOnPortal") or "").upper() in {"Y", "YES"}
    ]
    if len(found) != 1:
        raise ValidationError({
            "importId": "Requires exactly one verified GSTR-2B match marked ITC available by the source file.",
        })
    # A saved match is evidence for the original posted snapshot only. Refuse
    # silent changes to its date, taxable value or separate tax heads.
    original_heads = values(bill)
    row = found[0]
    if (
        key_period < bill.invoice_date.strftime("%Y-%m")
        or Decimal(str(row.get("taxable", "0"))) != bill.subtotal
        or any(
            Decimal(str(row.get(head, "0"))) != original_heads[head]
            for head in HEADS
        )
    ):
        raise ValidationError({
            "importId": "Saved GSTR-2B evidence differs from the current purchase tax snapshot.",
        })
    reason = str(request.data.get("eligibilityReason") or "").strip()
    if not 20 <= len(reason) <= 500:
        raise ValidationError({
            "eligibilityReason": "Record 20–500 characters of independent ITC eligibility review.",
        })
    available = available_heads(bill)
    if sum(available.values(), ZERO) <= ZERO:
        raise ValidationError({"billId": "No verified, unreturned purchase GST remains."})
    claim = GSTInputTaxClaim.objects.create(
        company=company, branch=bill.branch,
        bill=bill, import_batch=batch, period=key_period,
        cgst=available["cgst"], sgst=available["sgst"], igst=available["igst"],
        eligibility_reason=reason, approved_by=request.user,
    )
    return claim


def claim_info(claim):
    return {
        "id": str(claim.pk), "billId": str(claim.bill_id),
        "invoiceNumber": claim.bill.invoice_number,
        "period": claim.period, "cgst": str(claim.cgst),
        "sgst": str(claim.sgst), "igst": str(claim.igst),
        "total": str(claim.cgst + claim.sgst + claim.igst),
        "eligibilityReason": claim.eligibility_reason,
        "approvedAt": claim.created_at.isoformat(),
        "status": "Internally Approved — Not Portal Verified",
    }


@transaction.atomic
def reverse_credit_for_return(note):
    """Signal-driven proportional ITC reversal; exactly once per debit note."""
    claim = GSTInputTaxClaim.objects.filter(
        company_id=note.company_id, bill=note.bill,
    ).first()
    if claim is None:
        return None
    old = GSTInputTaxReversal.objects.filter(
        company_id=note.company_id, debit_note=note,
    ).first()
    if old:
        return old
    computed = debit_components(note)
    prior = GSTInputTaxReversal.objects.filter(
        company_id=note.company_id, claim=claim,
    )
    tax = {}
    for head in HEADS:
        used = sum((getattr(row, head) for row in prior), ZERO)
        available = max(ZERO, getattr(claim, head) - used)
        tax[head] = min(computed[head], available)
    if sum(tax.values(), ZERO) <= ZERO:
        return None
    return GSTInputTaxReversal.objects.create(
        company_id=note.company_id, branch=note.branch,
        claim=claim, debit_note=note, period=note.date.strftime("%Y-%m"),
        **tax,
    )


def credit_totals(company, period):
    approvals = GSTInputTaxClaim.objects.filter(company=company, period=period)
    reversals = GSTInputTaxReversal.objects.filter(company=company, period=period)
    claim = {
        head: sum((getattr(row, head) for row in approvals), ZERO)
        for head in HEADS
    }
    reversed_heads = {
        head: sum((getattr(row, head) for row in reversals), ZERO)
        for head in HEADS
    }
    return {"claimed": claim, "reversed": reversed_heads}
