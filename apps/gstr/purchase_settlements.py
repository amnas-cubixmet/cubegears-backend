"""Read-only settlement details for posted supplier bills in a GST period.

Cash/Bank/UPI money is posted ONLY in canonical supplier finance APIs.
GST preparation and ITC calculations are never affected by payment timing.
No workshop cash/bank account balances are exposed by this endpoint.
"""
from collections import defaultdict
from decimal import Decimal

from django.db.models import Sum
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import RolePermission
from apps.inventory.models import (
    SupplierDebitApplication, SupplierDebitNote, SupplierPaymentAllocation,
)
from .services import money, parse_period, supplier_queryset

ZERO = Decimal("0.00")


def purchase_settlements(company, period):
    """List period-dated posted bills with CURRENT payment allocation and payable.

    A later settlement changes 'outstanding' on the original bill but does not
    move its recorded purchase GST into the month of the payment. Advance
    application does not trigger another cash/bank entry.
    """
    bills = list(supplier_queryset(company, period))
    if not bills:
        return []

    bill_ids = [bill.pk for bill in bills]
    payments = defaultdict(lambda: {
        "cash": ZERO, "bank": ZERO, "other": ZERO, "total": ZERO,
    })
    allocations = (
        SupplierPaymentAllocation.objects.filter(
            company=company, bill_id__in=bill_ids,
        ).select_related("payment__account")
    )
    for allocation in allocations:
        account_type = allocation.payment.account.kind
        bucket = (
            "cash" if account_type == "Cash"
            else "bank" if account_type in {"Bank", "UPI"}
            else "other"
        )
        payments[allocation.bill_id][bucket] += allocation.amount
        payments[allocation.bill_id]["total"] += allocation.amount

    credits = defaultdict(lambda: ZERO)
    for bill_id, amount_value in (
        SupplierDebitNote.objects.filter(
            company=company, bill_id__in=bill_ids,
        ).values("bill_id").annotate(total=Sum("payable_reduction"))
        .values_list("bill_id", "total")
    ):
        credits[bill_id] += amount_value or ZERO
    for bill_id, amount_value in (
        SupplierDebitApplication.objects.filter(
            company=company, bill_id__in=bill_ids,
        ).values("bill_id").annotate(total=Sum("amount"))
        .values_list("bill_id", "total")
    ):
        credits[bill_id] += amount_value or ZERO

    rows = []
    for bill in bills:
        allocated = payments[bill.pk]
        payable_credit = credits[bill.pk]
        rows.append({
            "billId": str(bill.pk),
            "supplierId": str(bill.supplier_id),
            "invoiceNumber": bill.invoice_number,
            "invoiceDate": bill.invoice_date.isoformat(),
            "supplier": bill.supplier.name,
            "purchaseTotal": money(bill.total),
            "purchaseGST": money(bill.tax),
            "allocatedCash": money(allocated["cash"]),
            "allocatedBank": money(allocated["bank"]),
            "allocatedOther": money(allocated["other"]),
            "paidAllocated": money(allocated["total"]),
            "creditsApplied": money(payable_credit),
            "remainingCredit": money(max(
                ZERO, bill.total - allocated["total"] - payable_credit,
            )),
            "receivedIntoStock": bill.receive_into_stock,
        })
    return rows


class GSTRPurchaseSettlements(APIView):
    """Financial cross-reference only; not an official GSTR or tax ledger."""

    permission_classes = [RolePermission]
    permission_map = {"GET": ["gstr.view", "stock.view", "payments.view"]}

    def get(self, request):
        period = parse_period(request.query_params.get("period"))
        rows = purchase_settlements(request.user.company, period)
        return Response({
            "period": period["period"],
            "asOf": "current-ledger",
            "note": (
                "Payment allocations and remaining supplier credit are current "
                "ledger values, not historical month-end balances. Cash/Bank "
                "includes applied advances; no second cash entry is created. "
                "Payment status never determines GST or ITC eligibility."
            ),
            "rows": rows,
        })
