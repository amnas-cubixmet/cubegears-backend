"""Supplier returns / debit notes / unapplied credit settlement.

Posting a return never changes cash. A debit note first reduces the original
bill's *unpaid* payable; excess is a supplier refund claim. That credit can
either be applied to another posted bill or refunded to a recorded cash/bank
account (never both). All writes are atomic and idempotent.
"""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import re

from django.db import transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import RolePermission
from apps.companies.models import Company
from apps.inventory.branch_stock import locked_branch_balance, move_balance
from apps.inventory.models import (
    StockItem, StockMovement, Supplier, SupplierBill, SupplierDebitApplication,
    SupplierDebitNote, SupplierPayment, SupplierPaymentAllocation, SupplierRefund,
    WorkshopAccountEntry,
)
from .finance_api import (
    account_balance, account_for, bill_for, bill_paid, money, quantity, supplier_for,
)

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def request_key(data):
    key = str(data.get("requestKey") or data.get("idempotencyKey") or "").strip()
    if not 8 <= len(key) <= 120 or not re.fullmatch(r"[A-Za-z0-9_.:-]+", key):
        raise ValidationError({"requestKey": "Provide a unique ASCII request key (8–120 characters)."})
    return key


def bill_credit(bill):
    """Credit posted against the ORIGINAL bill and credit applied from other returns."""
    notes = SupplierDebitNote.objects.filter(
        company_id=bill.company_id, bill=bill,
    ).aggregate(total=Sum("payable_reduction"))["total"] or ZERO
    external = SupplierDebitApplication.objects.filter(
        company_id=bill.company_id, bill=bill,
    ).aggregate(total=Sum("amount"))["total"] or ZERO
    return notes + external


def bill_due(bill):
    return max(ZERO, bill.total - bill_paid(bill) - bill_credit(bill)) if bill.status == "Posted" else ZERO


def advance_available(payment):
    applied = SupplierPaymentAllocation.objects.filter(
        company_id=payment.company_id, payment=payment,
    ).aggregate(total=Sum("amount"))["total"] or ZERO
    refunded = SupplierRefund.objects.filter(
        company_id=payment.company_id, advance=payment,
    ).aggregate(total=Sum("amount"))["total"] or ZERO
    return max(ZERO, payment.amount - applied - refunded)


def debit_available(note):
    applied = note.applications.aggregate(total=Sum("amount"))["total"] or ZERO
    refunded = note.refunds.aggregate(total=Sum("amount"))["total"] or ZERO
    return max(ZERO, note.credit_amount - applied - refunded)


def debit_info(note):
    return {
        "id": str(note.pk), "supplierId": str(note.supplier_id),
        "billId": str(note.bill_id), "invoiceNumber": note.bill.invoice_number,
        "number": note.number, "date": note.date.isoformat(),
        "reason": note.reason, "lines": note.lines,
        "subtotal": str(note.subtotal), "tax": str(note.tax),
        "total": str(note.total),
        "payableReduction": str(note.payable_reduction),
        "creditAmount": str(note.credit_amount),
        "creditAvailable": str(debit_available(note)),
        "status": "Posted",
        "createdAt": note.created_at.isoformat(),
    }


def refund_info(refund):
    return {
        "id": str(refund.pk), "supplierId": str(refund.supplier_id),
        "debitNoteId": str(refund.debit_note_id) if refund.debit_note_id else None,
        "advanceId": str(refund.advance_id) if refund.advance_id else None,
        "accountId": str(refund.account_id), "amount": str(refund.amount),
        "method": refund.method, "reference": refund.reference,
        "refundedOn": refund.refunded_on.isoformat(),
        "createdAt": refund.created_at.isoformat(),
    }


def _return_line(bill, row, existing_notes):
    if not isinstance(row, dict):
        raise ValidationError({"lines": "Each return line must be an object."})
    try:
        index = int(row.get("lineIndex"))
    except (TypeError, ValueError):
        raise ValidationError({"lineIndex": "Select a valid purchased line."})
    if index < 0 or index >= len(bill.items):
        raise ValidationError({"lineIndex": "Line is not part of this supplier bill."})
    original = bill.items[index]
    ordered = quantity(original["quantity"])
    returned = sum((
        Decimal(line["quantity"])
        for note in existing_notes for line in note.lines
        if int(line["lineIndex"]) == index
    ), ZERO)
    qty = quantity(row.get("quantity"))
    if qty + returned > ordered:
        raise ValidationError({"quantity": "Return quantity exceeds the original unreturned purchase."})
    line_total = Decimal(original["total"])
    line_subtotal = Decimal(original["subtotal"])
    previous_gross = sum((
        Decimal(line["total"])
        for note in existing_notes for line in note.lines
        if int(line["lineIndex"]) == index
    ), ZERO)
    previous_subtotal = sum((
        Decimal(line["subtotal"])
        for note in existing_notes for line in note.lines
        if int(line["lineIndex"]) == index
    ), ZERO)
    if qty + returned == ordered:
        subtotal = line_subtotal - previous_subtotal
        gross = line_total - previous_gross
    else:
        subtotal = (line_subtotal * qty / ordered).quantize(CENT, rounding=ROUND_HALF_UP)
        gross = (line_total * qty / ordered).quantize(CENT, rounding=ROUND_HALF_UP)
        if subtotal > line_subtotal - previous_subtotal or gross > line_total - previous_gross:
            raise ValidationError({"quantity": "Return amount exceeds remaining purchased value."})
    if gross <= ZERO:
        raise ValidationError({"quantity": "Return has no remaining monetary value."})
    return {
        "lineIndex": index, "itemId": original.get("itemId"),
        "description": original.get("description", ""),
        "sku": original.get("sku", ""), "quantity": str(qty),
        "purchaseRate": original.get("purchaseRate", "0"),
        "subtotal": str(subtotal), "tax": str(gross - subtotal),
        "total": str(gross),
    }


class SupplierDebitNotes(APIView):
    permission_classes = [RolePermission]
    permission_map = {
        "GET": ["stock.view", "payments.view"],
        "POST": ["stock.edit", "payments.edit"],
    }

    def get(self, request):
        notes = SupplierDebitNote.objects.filter(
            company_id=request.user.company_id,
        ).select_related("bill", "supplier").order_by("-created_at")
        if request.query_params.get("supplier"):
            notes = notes.filter(supplier=supplier_for(request, request.query_params["supplier"]))
        return Response([debit_info(note) for note in notes[:200]])

    @transaction.atomic
    def post(self, request):
        key = request_key(request.data)
        Company.objects.select_for_update().get(pk=request.user.company_id)
        number = str(request.data.get("number") or "").strip()
        reason = str(request.data.get("reason") or "").strip()
        if not number or len(number) > 100:
            raise ValidationError({"number": "Provide a supplier debit note reference (1–100 characters)."})
        if len(reason) < 5 or len(reason) > 2000:
            raise ValidationError({"reason": "Explain this return (5–2000 characters)."})
        try:
            returned_on = date.fromisoformat(str(request.data.get("date") or timezone.localdate()))
        except (ValueError, TypeError):
            raise ValidationError({"date": "Use ISO YYYY-MM-DD date."})
        bill = bill_for(request, request.data.get("billId"), lock=True)
        if bill.status != "Posted":
            raise ValidationError({"billId": "Only a posted supplier bill can be returned."})
        existing = SupplierDebitNote.objects.filter(
            company_id=request.user.company_id, request_key=key,
        ).select_related("bill").first()
        if existing:
            if existing.bill_id != bill.pk or existing.number != number or existing.reason != reason or existing.date != returned_on:
                raise ValidationError({"requestKey": "This request key already belongs to a different debit note."})
            return Response(debit_info(existing), status=200)
        if SupplierDebitNote.objects.filter(
            company_id=request.user.company_id, supplier=bill.supplier,
            number__iexact=number,
        ).exists():
            raise ValidationError({"number": "This supplier debit note number already exists."})
        rows = request.data.get("lines")
        if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
            raise ValidationError({"lines": "Select 1–100 purchased items to return."})
        previous = list(SupplierDebitNote.objects.filter(
            company_id=request.user.company_id, bill=bill,
        ))
        seen = set()
        normalized = []
        for row in rows:
            parsed = _return_line(bill, row, previous)
            if parsed["lineIndex"] in seen:
                raise ValidationError({"lines": "A purchase item may appear only once per debit note."})
            seen.add(parsed["lineIndex"])
            normalized.append(parsed)
        subtotal = sum((Decimal(r["subtotal"]) for r in normalized), ZERO)
        tax = sum((Decimal(r["tax"]) for r in normalized), ZERO)
        total = subtotal + tax
        payable = min(total, bill_due(bill))
        note = SupplierDebitNote.objects.create(
            company_id=request.user.company_id, branch=bill.branch or request.user.branch,
            supplier=bill.supplier, bill=bill, request_key=key,
            number=number, date=returned_on, reason=reason, lines=normalized,
            subtotal=subtotal, tax=tax, total=total,
            payable_reduction=payable, credit_amount=total - payable,
            created_by=request.user,
        )
        if bill.receive_into_stock:
            for line in normalized:
                item_id = line["itemId"]
                if not item_id:
                    raise ValidationError({"lines": "Original received item is missing; return requires reconciliation."})
                item = get_object_or_404(
                    StockItem.objects.select_for_update(),
                    company_id=request.user.company_id, pk=item_id,
                )
                branch = bill.branch or request.user.branch
                balance = locked_branch_balance(item, branch)
                qty = Decimal(line["quantity"])
                move_balance(balance, -qty)
                item.on_hand -= qty
                item.save(update_fields=["on_hand", "updated_at"])
                StockMovement.objects.create(
                    company_id=request.user.company_id, branch=branch,
                    item=item, movement_type="purchase_return", quantity=-qty,
                    unit_cost=Decimal(line["purchaseRate"]),
                    unit_selling_price=item.selling_price,
                    reference="SUPRETURN:" + str(note.pk),
                    note="Debit note " + number, created_by=request.user,
                )
        return Response(debit_info(note), status=201)


class SupplierDebitApply(APIView):
    permission_classes = [RolePermission]
    permission_map = {"POST": ["stock.view", "payments.edit"]}

    @transaction.atomic
    def post(self, request, pk):
        key = request_key(request.data)
        Company.objects.select_for_update().get(pk=request.user.company_id)
        note = get_object_or_404(
            SupplierDebitNote.objects.select_for_update(),
            company_id=request.user.company_id, pk=pk,
        )
        bill = bill_for(request, request.data.get("billId"), lock=True)
        amount = money(request.data.get("amount"), "amount", positive=True)
        if bill.status != "Posted" or bill.supplier_id != note.supplier_id or bill.pk == note.bill_id:
            raise ValidationError({"billId": "Select a different posted bill for the same supplier."})
        previous = SupplierDebitApplication.objects.filter(
            company_id=request.user.company_id, request_key=key,
        ).first()
        if previous:
            if previous.debit_note_id != note.pk or previous.bill_id != bill.pk or previous.amount != amount:
                raise ValidationError({"requestKey": "This application key belongs to another transaction."})
            return Response({
                "id": str(previous.pk), "applied": str(previous.amount),
                "billOutstanding": str(bill_due(bill)),
                "creditAvailable": str(debit_available(note)),
            }, status=200)
        if amount > debit_available(note):
            raise ValidationError({"amount": "Supplier debit credit is insufficient."})
        if amount > bill_due(bill):
            raise ValidationError({"amount": "Amount exceeds the target bill's outstanding balance."})
        allocation = SupplierDebitApplication.objects.create(
            company_id=request.user.company_id, branch=request.user.branch,
            debit_note=note, bill=bill, amount=amount, request_key=key,
            created_by=request.user,
        )
        return Response({
            "id": str(allocation.pk), "applied": str(amount),
            "billOutstanding": str(bill_due(bill)),
            "creditAvailable": str(debit_available(note)),
            "note": "Supplier credit applied; cash/bank balance is unchanged.",
        }, status=201)


class SupplierRefunds(APIView):
    permission_classes = [RolePermission]
    permission_map = {
        "GET": ["stock.view", "payments.view"],
        "POST": ["stock.view", "payments.create"],
    }

    def get(self, request):
        qs = SupplierRefund.objects.filter(
            company_id=request.user.company_id,
        ).select_related("supplier", "account").order_by("-created_at")
        if request.query_params.get("supplier"):
            qs = qs.filter(supplier=supplier_for(request, request.query_params["supplier"]))
        return Response([refund_info(refund) for refund in qs[:200]])

    @transaction.atomic
    def post(self, request):
        key = request_key(request.data)
        Company.objects.select_for_update().get(pk=request.user.company_id)
        amount = money(request.data.get("amount"), "amount", positive=True)
        account = account_for(request, request.data.get("accountId"), lock=True)
        if not account.is_active:
            raise ValidationError({"accountId": "Choose an active account."})
        method = str(request.data.get("method") or "").strip()
        required_kind = {"Cash": "Cash", "Bank Transfer": "Bank", "UPI": "UPI"}.get(method)
        if required_kind != account.kind:
            raise ValidationError({"method": "Cash/Bank/UPI method must match the destination account."})
        reference = str(request.data.get("reference") or "").strip()
        if len(reference) > 120:
            raise ValidationError({"reference": "Reference exceeds 120 characters."})
        try:
            refunded_on = date.fromisoformat(str(request.data.get("refundedOn") or timezone.localdate()))
        except (ValueError, TypeError):
            raise ValidationError({"refundedOn": "Use ISO YYYY-MM-DD date."})
        debit_id = request.data.get("debitNoteId")
        advance_id = request.data.get("advanceId")
        if bool(debit_id) == bool(advance_id):
            raise ValidationError({"source": "Choose exactly one debit note or unapplied supplier advance."})
        note = get_object_or_404(
            SupplierDebitNote.objects.select_for_update(),
            pk=debit_id, company_id=request.user.company_id,
        ) if debit_id else None
        advance = get_object_or_404(
            SupplierPayment.objects.select_for_update(),
            pk=advance_id, company_id=request.user.company_id, kind="Advance",
        ) if advance_id else None
        supplier_id = note.supplier_id if note else advance.supplier_id
        existing = SupplierRefund.objects.filter(
            company_id=request.user.company_id, request_key=key,
        ).first()
        if existing:
            if (existing.debit_note_id != (note.pk if note else None)
                    or existing.advance_id != (advance.pk if advance else None)
                    or existing.account_id != account.pk
                    or existing.amount != amount or existing.method != method):
                raise ValidationError({"requestKey": "This refund key already belongs to another transaction."})
            return Response(refund_info(existing), status=200)
        available = debit_available(note) if note else advance_available(advance)
        if amount > available:
            raise ValidationError({"amount": "Refund exceeds available supplier credit or advance."})
        refund = SupplierRefund.objects.create(
            company_id=request.user.company_id, branch=request.user.branch,
            supplier_id=supplier_id, debit_note=note, advance=advance,
            account=account, amount=amount, method=method,
            reference=reference, refunded_on=refunded_on,
            request_key=key, created_by=request.user,
        )
        WorkshopAccountEntry.objects.create(
            company_id=request.user.company_id, branch=request.user.branch,
            account=account, amount=amount, entry_type="Supplier Refund",
            source_key="supplier-refund:" + str(refund.pk), reference=reference,
            posted_at=timezone.now(), created_by=request.user,
        )
        return Response({
            **refund_info(refund),
            "recordedAccountBalance": str(account_balance(account)),
            "creditAvailable": str(debit_available(note) if note else advance_available(advance)),
        }, status=201)


class SupplierPayables(APIView):
    permission_classes = [RolePermission]
    permission_map = {"GET": ["stock.view", "payments.view"]}

    def get(self, request):
        supplier_id = request.query_params.get("supplier")
        if supplier_id:
            supplier_for(request, supplier_id)
        bills = SupplierBill.objects.filter(
            company_id=request.user.company_id, status="Posted",
        ).select_related("supplier").order_by("due_date", "invoice_date", "id")
        if supplier_id:
            bills = bills.filter(supplier_id=supplier_id)
        today = timezone.localdate()
        bands = {name: ZERO for name in ("current", "1-30", "31-60", "61-90", "90+", "undated")}
        by_supplier = {}
        entries = []
        for bill in bills:
            due = bill_due(bill)
            if not due:
                continue
            if not bill.due_date:
                band = "undated"
            else:
                days = (today - bill.due_date).days
                band = "current" if days <= 0 else "1-30" if days <= 30 else "31-60" if days <= 60 else "61-90" if days <= 90 else "90+"
            bands[band] += due
            row = by_supplier.setdefault(str(bill.supplier_id), {
                "supplierId": str(bill.supplier_id), "supplierName": bill.supplier.name,
                "outstanding": ZERO, "overdue": ZERO,
            })
            row["outstanding"] += due
            if band not in ("current", "undated"):
                row["overdue"] += due
            entries.append({
                "billId": str(bill.pk), "supplierId": str(bill.supplier_id),
                "supplierName": bill.supplier.name, "invoiceNumber": bill.invoice_number,
                "invoiceDate": bill.invoice_date.isoformat(),
                "dueDate": bill.due_date.isoformat() if bill.due_date else None,
                "outstanding": str(due), "band": band,
            })
        return Response({
            "asOf": today.isoformat(),
            "totalOutstanding": str(sum(bands.values(), ZERO)),
            "aging": {name: str(amount) for name, amount in bands.items()},
            "suppliers": [{
                **row, "outstanding": str(row["outstanding"]),
                "overdue": str(row["overdue"]),
            } for row in by_supplier.values()],
            "bills": entries[:500],
            "notice": "Unapplied advances and debit credits are separate until explicitly allocated.",
        })
