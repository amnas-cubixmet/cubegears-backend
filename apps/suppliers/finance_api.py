"""Supplier accounting API (canonical suppliers app).

Bill posting creates payable exactly once; only posting a supplier payment
changes recorded cash/bank balances. Advance allocations never move cash twice.
Legacy PurchaseOrders, Expenses, Payments, and Stock movements stay intact.
"""
from datetime import date
from uuid import uuid4
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.db import transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import ValidationError, PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import RolePermission, user_has_permission
from apps.jobs.models import Job
from .purchase_gst import purchase_gst_context, purchase_line_components, purchase_bill_components
from .manual_gst import manual_purchase_components, validate_manual_purchase_lines
from apps.inventory.models import (
    Supplier, SupplierBill, SupplierPayment, SupplierPaymentAllocation, SupplierLinePaymentAllocation,
    StockItem, StockMovement, WorkshopFinancialAccount, WorkshopAccountEntry,
)

CENT = Decimal("0.01")


def money(value, label, *, positive=False):
    try:
        amount = Decimal(str(value))
    except (TypeError, ValueError, InvalidOperation):
        raise ValidationError({label: "Enter a valid decimal amount."})
    if not amount.is_finite() or amount >= Decimal("100000000000") or amount < 0:
        raise ValidationError({label: "Amount must be nonnegative and within supported limits."})
    if amount.as_tuple().exponent < -2 or (positive and amount <= 0):
        raise ValidationError({label: "Use a positive amount with at most two decimals."})
    return amount.quantize(CENT)


def quantity(value):
    result = money(value, "quantity", positive=True)
    if result > Decimal("1000000"):
        raise ValidationError({"quantity": "Quantity exceeds the permitted limit."})
    return result


def supplier_for(request, pk):
    return get_object_or_404(Supplier, pk=pk, company_id=request.user.company_id)


def bill_for(request, pk, *, lock=False):
    qs = SupplierBill.objects
    if lock:
        qs = qs.select_for_update()
    return get_object_or_404(qs, pk=pk, company_id=request.user.company_id)


def account_for(request, pk, *, lock=False):
    qs = WorkshopFinancialAccount.objects
    if lock:
        qs = qs.select_for_update()
    return get_object_or_404(qs, pk=pk, company_id=request.user.company_id)


def account_balance(account):
    recorded = WorkshopAccountEntry.objects.filter(
        company_id=account.company_id, account=account,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
    return account.opening_balance + recorded


def bill_paid(bill):
    return SupplierPaymentAllocation.objects.filter(
        company_id=bill.company_id, bill=bill,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")


def bill_info(bill):
    paid = bill_paid(bill)
    due = max(Decimal("0"), bill.total - paid) if bill.status == "Posted" else Decimal("0")
    status = "Draft" if bill.status == "Draft" else "Paid" if due == 0 else "Partially Paid" if paid else "Unpaid"
    item_paid = {
        row["line_index"]: row["total"]
        for row in SupplierLinePaymentAllocation.objects.filter(
            company_id=bill.company_id, allocation__bill=bill,
        ).values("line_index").annotate(total=Sum("amount"))
    }
    items = []
    for index, row in enumerate(bill.items):
        applied = item_paid.get(index, Decimal("0"))
        items.append({
            **row, "lineIndex": index, "paidAllocation": str(applied.quantize(CENT)),
            "lineOutstanding": str(max(Decimal("0"), Decimal(row["total"]) - applied).quantize(CENT)),
        })
    return {
        **purchase_bill_components(bill.items, bill.tax),
        "id": str(bill.pk), "supplierId": str(bill.supplier_id),
        "supplierName": bill.supplier.name,
        "purchasedBy": bill.created_by.name if bill.created_by_id else "",
        "invoiceNumber": bill.invoice_number, "invoiceDate": bill.invoice_date.isoformat(),
        "dueDate": bill.due_date.isoformat() if bill.due_date else None,
        "relatedJobId": str(bill.related_job_id) if bill.related_job_id else None,
        "status": status, "postingStatus": bill.status, "items": items,
        "subtotal": str(bill.subtotal), "tax": str(bill.tax),
        "total": str(bill.total.quantize(CENT)), "paid": str(paid.quantize(CENT)),
        "outstanding": str(due.quantize(CENT)),
        "receiveIntoStock": bill.receive_into_stock,
        "postedAt": bill.posted_at.isoformat() if bill.posted_at else None,
        "createdAt": bill.created_at.isoformat(),
    }


def supplier_summary(request, supplier):
    bills = list(SupplierBill.objects.filter(company_id=request.user.company_id, supplier=supplier, status="Posted"))
    amount_paid = Decimal("0")
    total = Decimal("0")
    outstanding = Decimal("0")
    overdue = Decimal("0")
    for bill in bills:
        paid = bill_paid(bill)
        remaining = max(Decimal("0"), bill.total - paid)
        amount_paid += paid
        total += bill.total
        outstanding += remaining
        if bill.due_date and bill.due_date < timezone.localdate():
            overdue += remaining
    advances = Decimal("0")
    for payment in SupplierPayment.objects.filter(company_id=request.user.company_id, supplier=supplier, kind="Advance"):
        applied = payment.allocations.aggregate(total=Sum("amount"))["total"] or Decimal("0")
        advances += max(Decimal("0"), payment.amount - applied)
    return {
        "supplierId": str(supplier.pk), "supplierName": supplier.name,
        "totalPurchases": str(total.quantize(CENT)), "totalPaymentsApplied": str(amount_paid.quantize(CENT)),
        "outstanding": str(outstanding.quantize(CENT)), "overdue": str(overdue.quantize(CENT)),
        "availableAdvance": str(advances.quantize(CENT)),
        "totalUnappliedAdvances": str(advances.quantize(CENT)),
    }


def prepare_items(request, rows, receive_into_stock, tax_context=None):
    if not isinstance(rows, list) or not rows or len(rows) > 100:
        raise ValidationError({"items": "Provide 1–100 purchase lines."})
    context = tax_context or {"taxMode": "legacy", "placeOfSupply": "", "supplierStateCode": ""}
    normalized = []
    subtotal = Decimal("0")
    tax_total = Decimal("0")
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValidationError({"items": f"Line {index + 1} must be an object."})
        count = quantity(row.get("quantity"))
        cost = money(row.get("purchaseRate"), "purchaseRate")
        gst = money(row.get("gstRate", "0"), "gstRate")
        if gst > 100:
            raise ValidationError({"gstRate": "GST cannot exceed 100%."})
        update_price = row.get("updateItemPrices") is True
        selling_price = None
        if update_price:
            selling_price = money(row.get("sellingPrice"), "sellingPrice")
        item_id = row.get("itemId")
        if update_price and not item_id:
            raise ValidationError({"itemId": "Updating catalog prices requires a stock item."})
        item = None
        if item_id:
            item = get_object_or_404(StockItem, pk=item_id, company_id=request.user.company_id)
            if item.branch_id and item.branch_id != request.user.branch_id:
                raise ValidationError({"itemId": "The stock item belongs to another branch."})
        if receive_into_stock and item is None:
            raise ValidationError({"itemId": "Select an existing stock item to receive inventory."})
        description = str(row.get("description") or (item.name if item else "")).strip()
        if not description or len(description) > 180:
            raise ValidationError({"description": "Enter a valid product name (1–180 characters)."})
        line_subtotal = (count * cost).quantize(CENT, rounding=ROUND_HALF_UP)
        explicit_components = "taxComponents" in row
        if explicit_components:
            components = manual_purchase_components(
                line_subtotal, row["taxComponents"], context,
                str(row.get("gstTreatment") or ""),
                str(row.get("zeroRatingBasis") or ""),
            )
            gst = Decimal(components["gstRate"])
        else:
            # Backwards-compatible route for legacy clients that submit a
            # single combined GST rate. New UI must send taxComponents.
            components = purchase_line_components(
                line_subtotal, gst, context, str(row.get("gstTreatment") or "")
            )
        line_tax = components["tax"]
        subtotal += line_subtotal
        tax_total += line_tax
        normalized.append({
            "lineId": str(uuid4()), "itemId": str(item.pk) if item else None,
            "description": description, "partNumber": item.part_number if item else "",
            "sku": item.sku if item else "", "quantity": str(count),
            "purchaseRate": str(cost), "gstRate": str(gst),
            "updateItemPrices": update_price,
            **({"sellingPrice": str(selling_price)} if update_price else {}),
            "taxMode": context["taxMode"], "placeOfSupply": context["placeOfSupply"],
            "supplierStateCode": context["supplierStateCode"],
            "gstTreatment": components["gstTreatment"],
            **({key: components[key] for key in
                ("taxComponents", "taxValidationStatus", "taxValidationMessage", "zeroRatingBasis")}
               if explicit_components else {}),
            **{key: str(components[key].quantize(CENT)) for key in ("cgst", "sgst", "utgst", "igst")},
            "subtotal": str(line_subtotal), "tax": str(line_tax),
            "total": str(line_subtotal + line_tax),
        })
    return normalized, subtotal, tax_total


def record_supplier_payment(request, data):
    """Only canonical supplier payment ledger changes money; caller wraps atomically."""
    supplier = supplier_for(request, data.get("supplierId"))
    account = account_for(request, data.get("accountId"), lock=True)
    if not account.is_active:
        raise ValidationError({"accountId": "Select an active cash/bank account."})
    amount = money(data.get("amount"), "amount", positive=True)
    kind = data.get("kind", "Payment")
    if kind not in {"Payment", "Advance"}:
        raise ValidationError({"kind": "Choose Payment or Advance."})
    method = str(data.get("method") or "").strip()
    if method not in {"Cash", "Bank Transfer", "UPI"}:
        raise ValidationError({"method": "Select Cash, Bank Transfer, or UPI."})
    if {"Cash": "Cash", "Bank Transfer": "Bank", "UPI": "UPI"}[method] != account.kind:
        raise ValidationError({"accountId": "Payment method does not match the account type."})
    key = str(data.get("idempotencyKey") or "").strip()
    if len(key) < 8 or len(key) > 120:
        raise ValidationError({"idempotencyKey": "A unique request key (8–120 characters) is required."})
    existing = SupplierPayment.objects.filter(company_id=request.user.company_id, idempotency_key=key).first()
    if existing:
        raise ValidationError({"idempotencyKey": "This supplier payment was already recorded."})
    try:
        paid_on = date.fromisoformat(str(data.get("paidOn") or timezone.localdate()))
    except (TypeError, ValueError):
        raise ValidationError({"paidOn": "Enter a valid payment date."})
    reference = str(data.get("reference") or "").strip()
    if len(reference) > 120:
        raise ValidationError({"reference": "Reference is too long."})
    if account_balance(account) < amount:
        raise ValidationError({"accountId": "Insufficient recorded account balance."})
    rows = data.get("allocations", [])
    if not isinstance(rows, list):
        raise ValidationError({"allocations": "Allocations must be a list."})
    if kind == "Advance" and rows:
        raise ValidationError({"allocations": "Record the advance first; apply it to bills separately."})
    if kind == "Payment" and not rows:
        raise ValidationError({"allocations": "Choose at least one posted bill, or record an Advance."})
    validated = []
    seen = set()
    allocated = Decimal("0")
    for row in rows:
        if not isinstance(row, dict) or not row.get("billId"):
            raise ValidationError({"allocations": "Each allocation needs a billId."})
        bill_id = str(row["billId"])
        if bill_id in seen:
            raise ValidationError({"allocations": "Cannot allocate the same bill twice in one payment."})
        seen.add(bill_id)
        bill = bill_for(request, bill_id, lock=True)
        if bill.supplier_id != supplier.pk or bill.status != "Posted":
            raise ValidationError({"allocations": "Bill is not a posted bill for this supplier."})
        value = money(row.get("amount"), "amount", positive=True)
        if value > bill.total - bill_paid(bill):
            raise ValidationError({"allocations": "Allocation exceeds the bill outstanding."})
        allocated += value
        line_rows = row.get("lineAllocations", [])
        if not isinstance(line_rows, list):
            raise ValidationError({"lineAllocations": "Item allocations must be a list."})
        details = []
        if line_rows:
            seen_lines = set()
            detail_sum = Decimal("0")
            for detail in line_rows:
                try:
                    index = int(detail.get("lineIndex"))
                except (TypeError, ValueError, AttributeError):
                    raise ValidationError({"lineAllocations": "Choose a valid line index."})
                if not isinstance(detail, dict) or index < 0 or index >= len(bill.items) or index in seen_lines:
                    raise ValidationError({"lineAllocations": "Invalid or duplicate purchase line."})
                seen_lines.add(index)
                line_amount = money(detail.get("amount"), "lineAllocations", positive=True)
                previously_paid = SupplierLinePaymentAllocation.objects.filter(
                    company_id=request.user.company_id, allocation__bill=bill, line_index=index,
                ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
                line_total = Decimal(bill.items[index]["total"])
                if line_amount > line_total - previously_paid:
                    raise ValidationError({"lineAllocations": "Amount exceeds the remaining line total."})
                detail_sum += line_amount
                details.append((index, line_amount))
            if detail_sum != value:
                raise ValidationError({"lineAllocations": "Item allocations must equal this bill allocation."})
        validated.append((bill, value, details))
    if kind == "Payment" and allocated != amount:
        raise ValidationError({"allocations": "Allocations must total the payment amount."})
    payment = SupplierPayment.objects.create(
        company_id=request.user.company_id, branch=request.user.branch,
        supplier=supplier, account=account, amount=amount, kind=kind,
        method=method, reference=reference, paid_on=paid_on,
        idempotency_key=key, created_by=request.user,
    )
    for bill, allocated_amount, details in validated:
        allocation = SupplierPaymentAllocation.objects.create(
            company_id=request.user.company_id, branch=request.user.branch,
            bill=bill, payment=payment, amount=allocated_amount,
        )
        for index, line_amount in details:
            SupplierLinePaymentAllocation.objects.create(
                company_id=request.user.company_id, branch=request.user.branch,
                allocation=allocation, line_index=index, amount=line_amount,
            )
    WorkshopAccountEntry.objects.create(
        company_id=request.user.company_id, branch=request.user.branch,
        account=account, amount=-amount, entry_type="Supplier " + kind,
        source_key=f"supplier-payment:{payment.pk}", reference=reference,
        posted_at=timezone.now(), created_by=request.user,
    )
    return {
        "id": str(payment.pk), "amount": str(payment.amount),
        "kind": payment.kind, "applied": str(allocated),
        "availableAdvance": str(amount) if kind == "Advance" else "0.00",
        "recordedAccountBalance": str(account_balance(account)),
    }



def finalize_supplier_bill(request, bill, payment_data=None, payments_data=None):
    """Post a bill, optional cash settlement and receipt in one DB transaction."""
    if bill.status != "Draft":
        raise ValidationError({"bill": "Supplier bill was already posted; inventory and liability were not posted again."})
    # Validate explicitly chosen components before changing any stock or ledger.
    validate_manual_purchase_lines(bill.items)
    if bill.receive_into_stock:
        for row in bill.items:
            item = get_object_or_404(StockItem.objects.select_for_update(),
                pk=row["itemId"], company_id=request.user.company_id)
            if item.branch_id and item.branch_id != bill.branch_id:
                raise ValidationError({"itemId": "Stock item branch no longer matches the purchase."})
            count = Decimal(row["quantity"])
            cost = Decimal(row["purchaseRate"])
            item.on_hand += count
            update_fields = ["on_hand", "updated_at"]
            if row.get("updateItemPrices") is True:
                item.cost_price = cost
                item.selling_price = Decimal(row["sellingPrice"])
                item.tax = Decimal(row["gstRate"])
                update_fields.extend(["cost_price", "selling_price", "tax"])
            item.save(update_fields=update_fields)
            StockMovement.objects.create(
                company_id=request.user.company_id, branch=bill.branch,
                item=item, movement_type="purchase", quantity=count,
                unit_cost=cost, unit_selling_price=item.selling_price,
                reference=f"SUPBILL:{bill.pk}", job=bill.related_job,
                note=f"Supplier invoice {bill.invoice_number}",
                created_by=request.user,
            )
    bill.status = "Posted"
    bill.posted_at = timezone.now()
    bill.save(update_fields=["status", "posted_at", "updated_at"])
    result = bill_info(bill)
    if payment_data is not None and payments_data is not None:
        raise ValidationError({"payments": "Use payment or payments, not both."})
    if payments_data is not None:
        if not isinstance(payments_data, list) or not (1 <= len(payments_data) <= 5):
            raise ValidationError({"payments": "Provide 1–5 cash/bank payment splits."})
        if any(not isinstance(row, dict) for row in payments_data):
            raise ValidationError({"payments": "Every split must be an object."})
        total_split = sum(
            (money(row.get("amount"), "amount", positive=True) for row in payments_data),
            Decimal("0"),
        )
        if total_split > bill.total:
            raise ValidationError({"payments": "Paid amounts exceed supplier bill total."})
        posted = []
        for row in payments_data:
            if row.get("kind", "Payment") != "Payment" or "allocations" in row:
                raise ValidationError({"payments": "Split payments must settle this bill, not advances or other bills."})
            amount = money(row.get("amount"), "amount", positive=True)
            payload = {
                **row, "kind": "Payment", "supplierId": str(bill.supplier_id),
                "allocations": [{"billId": str(bill.pk), "amount": str(amount)}],
            }
            posted.append(record_supplier_payment(request, payload))
        return {**bill_info(bill), "payments": posted}
    if payment_data is not None:
        if not isinstance(payment_data, dict):
            raise ValidationError({"payment": "Payment must be a valid object."})
        if payment_data.get("supplierId") and str(payment_data["supplierId"]) != str(bill.supplier_id):
            raise ValidationError({"payment": "Payment Supplier does not match the bill."})
        payload = {**payment_data, "supplierId": str(bill.supplier_id)}
        if "allocations" not in payload:
            payload["allocations"] = [{"billId": str(bill.pk), "amount": payload.get("amount")}]
        else:
            if not isinstance(payload["allocations"], list):
                raise ValidationError({"allocations": "Allocations must be a list."})
            payload["allocations"] = [
                {**row, "billId": row.get("billId") or str(bill.pk)}
                if isinstance(row, dict) else row
                for row in payload["allocations"]
            ]
        result["payment"] = record_supplier_payment(request, payload)
        result = {**bill_info(bill), "payment": result["payment"]}
    return result



class SupplierPurchaseGSTOptions(APIView):
    """State names and rate presets, never an HSN/SAC classification engine."""
    permission_classes = [RolePermission]
    permission_map = {"GET": "stock.view"}

    def get(self, request):
        from apps.jobs.india_gst import STATE_NAMES

        return Response({
            "states": [
                {"code": code, "name": name.title()}
                for name, code in sorted(STATE_NAMES.items())
            ],
            "componentChoices": [
                *[{"value": f"cgst:{rate}", "label": f"CGST {rate}%"} for rate in ("2.5", "5", "9", "18")],
                *[{"value": f"sgst:{rate}", "label": f"SGST {rate}%"} for rate in ("2.5", "5", "9", "18")],
                *[{"value": f"utgst:{rate}", "label": f"UTGST {rate}%"} for rate in ("2.5", "5", "9", "18")],
                *[{"value": f"igst:{rate}", "label": f"IGST {rate}%"} for rate in ("5", "18")],
                {"value": "zero", "label": "0% — Select Classification"},
            ],
            "zeroClassifications": ["exempt", "nil_rated", "zero_rated", "non_gst"],
            "rates": [  # Legacy combined-rate API; deprecated for new UI.
                {"total": "0", "cgst": "0", "stateTax": "0", "igst": "0"},
                {"total": "5", "cgst": "2.5", "stateTax": "2.5", "igst": "5"},
                {"total": "18", "cgst": "9", "stateTax": "9", "igst": "18"},
            ],
            "note": "Confirm HSN/SAC classification and tax treatment before posting.",
        })


class AccountCollection(APIView):
    permission_classes = [RolePermission]
    permission_map = {"GET": "payments.view", "POST": "payments.create"}

    def get(self, request):
        accounts = WorkshopFinancialAccount.objects.filter(company_id=request.user.company_id).order_by("name")
        return Response([{
            "id": str(account.pk), "name": account.name, "kind": account.kind,
            "openingBalance": str(account.opening_balance),
            "recordedBalance": str(account_balance(account)),
            "isActive": account.is_active,
            "balanceType": "recorded-ledger-not-live-bank",
        } for account in accounts])

    @transaction.atomic
    def post(self, request):
        name = str(request.data.get("name") or "").strip()
        kind = request.data.get("kind")
        if not name or len(name) > 120:
            raise ValidationError({"name": "Name must be 1–120 characters."})
        if kind not in {"Cash", "Bank", "UPI"}:
            raise ValidationError({"kind": "Choose Cash, Bank, or UPI."})
        opening = money(request.data.get("openingBalance", "0"), "openingBalance")
        if WorkshopFinancialAccount.objects.filter(company_id=request.user.company_id, name__iexact=name).exists():
            raise ValidationError({"name": "An account with this name already exists."})
        account = WorkshopFinancialAccount.objects.create(
            company_id=request.user.company_id, branch=request.user.branch,
            name=name, kind=kind, opening_balance=opening, created_by=request.user,
        )
        return Response({"id": str(account.pk), "name": account.name, "kind": account.kind,
                         "recordedBalance": str(opening)}, status=201)


class AccountLedger(APIView):
    permission_classes = [RolePermission]
    permission_map = {"GET": "payments.view"}

    def get(self, request, pk):
        account = account_for(request, pk)
        opening = account.opening_balance
        balance = opening
        entries = []
        for entry in WorkshopAccountEntry.objects.filter(company_id=request.user.company_id, account=account).order_by("posted_at", "created_at", "pk"):
            balance += entry.amount
            entries.append({
                "id": str(entry.pk), "date": entry.posted_at.isoformat(),
                "type": entry.entry_type, "reference": entry.reference,
                "moneyIn": str(max(entry.amount, Decimal("0"))),
                "moneyOut": str(max(-entry.amount, Decimal("0"))),
                "runningBalance": str(balance),
            })
        return Response({"accountId": str(account.pk), "openingBalance": str(opening),
                         "recordedBalance": str(balance), "balanceType": "recorded-ledger-not-live-bank",
                         "entries": entries})


class SupplierBillCollection(APIView):
    permission_classes = [RolePermission]
    permission_map = {"GET": ["stock.view", "payments.view"], "POST": ["stock.create", "payments.create"]}

    def get(self, request):
        qs = SupplierBill.objects.filter(company_id=request.user.company_id).select_related("supplier", "created_by").order_by("-created_at")
        supplier_id = request.query_params.get("supplier")
        if supplier_id:
            supplier = supplier_for(request, supplier_id)
            qs = qs.filter(supplier=supplier)
        return Response([bill_info(bill) for bill in qs[:100]])

    @transaction.atomic
    def post(self, request):
        supplier = supplier_for(request, request.data.get("supplierId"))
        if not supplier.is_active:
            raise ValidationError({"supplierId": "Supplier is inactive."})
        # Lock the supplier while checking invoice references, including case variants.
        Supplier.objects.select_for_update().get(pk=supplier.pk)
        if request.data.get("postNow") is True and not user_has_permission(request.user, "stock.edit"):
            raise PermissionDenied("Purchase posting requires Inventory Edit permission.")
        invoice_number = str(request.data.get("invoiceNumber") or "").strip()
        if not invoice_number or len(invoice_number) > 100:
            raise ValidationError({"invoiceNumber": "Enter a supplier invoice reference (1–100 characters)."})
        try:
            invoice_date = date.fromisoformat(str(request.data.get("invoiceDate")))
            due_date = date.fromisoformat(str(request.data["dueDate"])) if request.data.get("dueDate") else None
        except (TypeError, ValueError):
            raise ValidationError({"invoiceDate": "Use ISO YYYY-MM-DD dates."})
        job = None
        if request.data.get("relatedJobId"):
            job = get_object_or_404(Job, pk=request.data["relatedJobId"], company_id=request.user.company_id)
        receive = request.data.get("receiveIntoStock") is True
        tax_context = purchase_gst_context(supplier, request.data.get("placeOfSupply"))
        items, subtotal, tax = prepare_items(request, request.data.get("items"), receive, tax_context)
        if SupplierBill.objects.filter(
            company_id=request.user.company_id, supplier=supplier,
            invoice_number__iexact=invoice_number,
        ).exists():
            raise ValidationError({"invoiceNumber": "This supplier invoice already exists."})
        bill = SupplierBill.objects.create(
            company_id=request.user.company_id, branch=request.user.branch,
            supplier=supplier, invoice_number=invoice_number,
            invoice_date=invoice_date, due_date=due_date, related_job=job,
            items=items, subtotal=subtotal, tax=tax, total=subtotal + tax,
            receive_into_stock=receive, created_by=request.user,
        )
        if request.data.get("postNow") is True:
            return Response(finalize_supplier_bill(
                request, bill, request.data.get("payment"),
                request.data.get("payments"),
            ), status=201)
        if request.data.get("payment") is not None or request.data.get("payments") is not None:
            raise ValidationError({"payment": "Immediate payments require postNow=true."})
        return Response(bill_info(bill), status=201)


class SupplierBillPost(APIView):
    permission_classes = [RolePermission]
    permission_map = {"POST": ["stock.edit", "payments.create"]}

    @transaction.atomic
    def post(self, request, pk):
        bill = bill_for(request, pk, lock=True)
        return Response(finalize_supplier_bill(request, bill, request.data.get("payment"), request.data.get("payments")))


class SupplierPaymentCollection(APIView):
    permission_classes = [RolePermission]
    permission_map = {"GET": ["stock.view", "payments.view"], "POST": ["stock.view", "payments.create"]}

    def get(self, request):
        qs = SupplierPayment.objects.filter(company_id=request.user.company_id).select_related("supplier", "account", "created_by").prefetch_related("allocations__bill")
        if request.query_params.get("supplier"):
            qs = qs.filter(supplier=supplier_for(request, request.query_params["supplier"]))
        result = []
        for payment in qs[:100]:
            applied = payment.allocations.aggregate(total=Sum("amount"))["total"] or Decimal("0")
            result.append({
                "id": str(payment.pk), "supplierId": str(payment.supplier_id),
                "supplierName": payment.supplier.name,
                "createdAt": payment.created_at.isoformat(),
                "recordedBy": payment.created_by.name if payment.created_by_id else "",
                "billReferences": [allocation.bill.invoice_number for allocation in payment.allocations.all()],
                "accountId": str(payment.account_id), "amount": str(payment.amount),
                "kind": payment.kind, "method": payment.method,
                "reference": payment.reference, "paidOn": payment.paid_on.isoformat(),
                "applied": str(applied), "unapplied": str(payment.amount - applied),
            })
        return Response(result)

    @transaction.atomic
    def post(self, request):
        return Response(record_supplier_payment(request, request.data), status=201)


class SupplierAdvanceApply(APIView):
    permission_classes = [RolePermission]
    permission_map = {"POST": ["stock.view", "payments.edit"]}

    @transaction.atomic
    def post(self, request, pk):
        payment = get_object_or_404(SupplierPayment.objects.select_for_update(),
            pk=pk, company_id=request.user.company_id, kind="Advance")
        bill = bill_for(request, request.data.get("billId"), lock=True)
        if bill.supplier_id != payment.supplier_id or bill.status != "Posted":
            raise ValidationError({"billId": "Choose a posted bill for the same supplier."})
        amount = money(request.data.get("amount"), "amount", positive=True)
        applied = payment.allocations.aggregate(total=Sum("amount"))["total"] or Decimal("0")
        if amount > payment.amount - applied:
            raise ValidationError({"amount": "Not enough unapplied supplier advance."})
        if amount > bill.total - bill_paid(bill):
            raise ValidationError({"amount": "Amount exceeds the remaining supplier bill."})
        if SupplierPaymentAllocation.objects.filter(payment=payment, bill=bill).exists():
            raise ValidationError({"billId": "Advance already applied to this bill. Use another bill."})
        allocation = SupplierPaymentAllocation.objects.create(
            company_id=request.user.company_id, branch=request.user.branch,
            payment=payment, bill=bill, amount=amount,
        )
        return Response({
            "allocationId": str(allocation.pk), "applied": str(amount),
            "billOutstanding": str(bill.total - bill_paid(bill)),
            "advanceRemaining": str(payment.amount - applied - amount),
            "note": "Advance applied without another cash/bank deduction.",
        }, status=201)


class SupplierSummary(APIView):
    permission_classes = [RolePermission]
    permission_map = {"GET": ["stock.view", "payments.view"]}

    def get(self, request, pk):
        return Response(supplier_summary(request, supplier_for(request, pk)))


class SupplierLedger(APIView):
    permission_classes = [RolePermission]
    permission_map = {"GET": ["stock.view", "payments.view"]}

    def get(self, request, pk):
        supplier = supplier_for(request, pk)
        events = []
        for bill in SupplierBill.objects.filter(company_id=request.user.company_id, supplier=supplier, status="Posted"):
            events.append({
                "date": bill.posted_at.isoformat(),
                "type": "Supplier Bill", "reference": bill.invoice_number,
                "payableIncrease": bill.total, "payableDecrease": Decimal("0"),
                "sourceId": str(bill.pk),
            })
        allocations = SupplierPaymentAllocation.objects.filter(
            company_id=request.user.company_id, payment__supplier=supplier,
        ).select_related("payment", "bill")
        for alloc in allocations:
            events.append({
                "date": alloc.created_at.isoformat(),
                "type": "Advance Application" if alloc.payment.kind == "Advance" else "Supplier Payment",
                "reference": alloc.bill.invoice_number,
                "payableIncrease": Decimal("0"), "payableDecrease": alloc.amount,
                "sourceId": str(alloc.payment_id),
            })
        events.sort(key=lambda row: (row["date"], row["type"], row["sourceId"]))
        running = Decimal("0")
        for event in events:
            running += event["payableIncrease"] - event["payableDecrease"]
            event["payableIncrease"] = str(event["payableIncrease"])
            event["payableDecrease"] = str(event["payableDecrease"])
            event["runningPayable"] = str(running)
        result = supplier_summary(request, supplier)
        result["entries"] = events
        result["signConvention"] = "Positive = supplier payable; advance is separate until applied."
        return Response(result)
