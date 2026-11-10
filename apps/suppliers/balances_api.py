"""Tenant-safe supplier balances for the complete supplier directory.

Always aggregate ALL posted bills; the purchase listing endpoint is intentionally
limited and must not be used to calculate financial liabilities. Draft orders
and advance payments are never counted as extra purchases.
"""
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import RolePermission
from apps.inventory.models import (
    Supplier, SupplierBill, SupplierPayment, SupplierPaymentAllocation,
    SupplierDebitNote, SupplierDebitApplication, SupplierRefund,
)

ZERO = Decimal("0.00")
CENT = Decimal("0.01")


def _totals_by(queryset, key, field):
    return {
        str(row[key]): row["amount"] or ZERO
        for row in queryset.values(key).annotate(amount=Sum(field))
    }


def supplier_balances(company_id):
    suppliers = list(Supplier.objects.filter(company_id=company_id).values("id", "name"))
    rows = {str(supplier["id"]): {
        "supplierId": str(supplier["id"]), "supplierName": supplier["name"],
        "totalPurchases": ZERO, "totalPaymentsApplied": ZERO,
        "creditsApplied": ZERO, "outstanding": ZERO, "overdue": ZERO,
        "availableAdvance": ZERO, "availableDebitCredit": ZERO,
        "postedBillCount": 0,
    } for supplier in suppliers}
    paid = _totals_by(
        SupplierPaymentAllocation.objects.filter(
            company_id=company_id, bill__status="Posted"
        ), "bill_id", "amount",
    )
    returned = _totals_by(
        SupplierDebitNote.objects.filter(
            company_id=company_id, bill__status="Posted"
        ), "bill_id", "payable_reduction",
    )
    applied_credit = _totals_by(
        SupplierDebitApplication.objects.filter(
            company_id=company_id, bill__status="Posted"
        ), "bill_id", "amount",
    )
    today = timezone.localdate()
    for bill in SupplierBill.objects.filter(
        company_id=company_id, status="Posted"
    ).values("id", "supplier_id", "total", "due_date"):
        item = rows[str(bill["supplier_id"])]
        key = str(bill["id"])
        paid_amount = paid.get(key, ZERO)
        credit_amount = returned.get(key, ZERO) + applied_credit.get(key, ZERO)
        due = max(ZERO, bill["total"] - paid_amount - credit_amount)
        item["totalPurchases"] += bill["total"]
        item["totalPaymentsApplied"] += paid_amount
        item["creditsApplied"] += credit_amount
        item["outstanding"] += due
        item["postedBillCount"] += 1
        if bill["due_date"] and bill["due_date"] < today:
            item["overdue"] += due

    advances = SupplierPayment.objects.filter(
        company_id=company_id, kind="Advance"
    ).values("id", "supplier_id", "amount")
    allocated_advances = _totals_by(
        SupplierPaymentAllocation.objects.filter(
            company_id=company_id, payment__kind="Advance"
        ), "payment_id", "amount",
    )
    refunded_advances = _totals_by(
        SupplierRefund.objects.filter(
            company_id=company_id, advance__isnull=False
        ), "advance_id", "amount",
    )
    for advance in advances:
        rows[str(advance["supplier_id"])]["availableAdvance"] += max(
            ZERO, advance["amount"] - allocated_advances.get(str(advance["id"]), ZERO)
            - refunded_advances.get(str(advance["id"]), ZERO),
        )

    note_uses = _totals_by(
        SupplierDebitApplication.objects.filter(company_id=company_id),
        "debit_note_id", "amount",
    )
    note_refunds = _totals_by(
        SupplierRefund.objects.filter(
            company_id=company_id, debit_note__isnull=False
        ), "debit_note_id", "amount",
    )
    for note in SupplierDebitNote.objects.filter(
        company_id=company_id
    ).values("id", "supplier_id", "credit_amount"):
        rows[str(note["supplier_id"])]["availableDebitCredit"] += max(
            ZERO, note["credit_amount"] - note_uses.get(str(note["id"]), ZERO)
            - note_refunds.get(str(note["id"]), ZERO),
        )

    return [{
        **row,
        **{key: str(value.quantize(CENT)) for key, value in row.items()
           if isinstance(value, Decimal)},
        "netExposureAfterAvailableCredits": str(
            (row["outstanding"] - row["availableAdvance"]
             - row["availableDebitCredit"]).quantize(CENT)
        ),
    } for row in rows.values()]


class SupplierBalanceCollection(APIView):
    permission_classes = [RolePermission]
    permission_map = {"GET": ["stock.view", "payments.view"]}

    def get(self, request):
        return Response(supplier_balances(request.user.company_id))
