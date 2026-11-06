"""Read-only unified payment history; never reposts a payment to the ledger.

Supplier bills are payables, not payments. This endpoint combines the already
posted canonical payment records without creating duplicate financial entries.
"""
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import RolePermission, user_has_permission
from apps.expenses.models import ExpenseSettlement
from apps.inventory.models import SupplierBill, SupplierPayment, SupplierPaymentAllocation
from apps.payments.models import Payment
from apps.payroll.daily_wage_models import WagePayment

CENT = Decimal("0.01")


class FinancePaymentActivity(APIView):
    permission_classes = [RolePermission]
    permission_map = {"GET": "payments.view"}

    def get(self, request):
        company = request.user.company
        entries = []
        # Customer collections and manually posted outgoing payments.
        for payment in Payment.objects.filter(company=company).select_related(
            "customer", "invoice", "account",
        ).order_by("-created_at")[:1000]:
            is_in = payment.direction == "IN"
            entries.append({
                "id": str(payment.pk), "source": "payment",
                "sourceId": str(payment.pk), "direction": payment.direction,
                "date": payment.date.isoformat(),
                "timestamp": payment.created_at.isoformat(),
                "customer": payment.customer.name if payment.customer_id else (
                    "Other payer" if is_in else payment.notes or "Other payee"
                ),
                "invoice": payment.invoice.number if payment.invoice_id else "",
                "method": payment.method, "amount": str(payment.amount),
                "reference": payment.reference,
                "category": payment.category or ("Customer Payment" if is_in else "Other Payment"),
                "status": payment.status,
                "account": payment.account.name if payment.account_id else "",
                "detailPath": f"/payments/{payment.pk}",
            })

        supplier_outstanding = Decimal("0")
        total_supplier_purchases = Decimal("0")
        supplier_payments = Decimal("0")
        if user_has_permission(request.user, "stock.view"):
            # A credit purchase changes the payable, not the cash/bank account.
            for bill in SupplierBill.objects.filter(
                company=company, status="Posted"
            ):
                total_supplier_purchases += bill.total
                credited = SupplierPaymentAllocation.objects.filter(
                    company=company, bill=bill
                ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
                supplier_outstanding += max(Decimal("0"), bill.total - credited)

            # Summary totals include ALL recorded payments, even when the
            # latest-history list is capped for responsiveness.
            supplier_payments = SupplierPayment.objects.filter(
                company=company
            ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
            for payment in SupplierPayment.objects.filter(
                company=company
            ).select_related("supplier", "account", "created_by").prefetch_related(
                "allocations__bill"
            ).order_by("-created_at")[:1000]:
                bills = [a.bill.invoice_number for a in payment.allocations.all()]
                entries.append({
                    "id": str(payment.pk), "source": "supplier",
                    "sourceId": str(payment.pk), "supplierId": str(payment.supplier_id),
                    "direction": "OUT", "date": payment.paid_on.isoformat(),
                    "timestamp": payment.created_at.isoformat(),
                    "customer": payment.supplier.name,
                    "invoice": ", ".join(bills) or "Supplier Advance",
                    "method": payment.method, "amount": str(payment.amount),
                    "reference": payment.reference, "status": "Completed",
                    "category": "Supplier Advance" if payment.kind == "Advance" else "Spare Parts / Supplier",
                    "account": payment.account.name,
                    "detailPath": f"/suppliers/payments?supplier={payment.supplier_id}",
                })

        if user_has_permission(request.user, "expenses.view"):
            for settlement in ExpenseSettlement.objects.filter(company=company).select_related(
                "expense", "account"
            ).order_by("-created_at")[:1000]:
                entries.append({
                    "id": str(settlement.pk), "source": "expense",
                    "sourceId": str(settlement.pk), "direction": "OUT",
                    "date": timezone.localtime(settlement.posted_at).date().isoformat(),
                    "timestamp": settlement.posted_at.isoformat(),
                    "customer": settlement.expense.vendor or "Workshop Expense",
                    "invoice": settlement.expense.description.split("\n", 1)[0][:100],
                    "method": settlement.method, "amount": str(settlement.amount),
                    "reference": settlement.reference,
                    "status": "Reversed" if settlement.reversed_at else "Completed",
                    "category": settlement.expense.category,
                    "account": settlement.account.name,
                    "detailPath": f"/expenses/{settlement.expense_id}",
                })
        if user_has_permission(request.user, "payroll.payments.view"):
            for payment in WagePayment.objects.filter(company=company).select_related(
                "employee", "account"
            ).order_by("-created_at")[:1000]:
                entries.append({
                    "id": str(payment.pk), "source": "wage",
                    "sourceId": str(payment.pk), "direction": "OUT",
                    "date": payment.payment_date.isoformat(),
                    "timestamp": payment.created_at.isoformat(),
                    "customer": payment.employee.name,
                    "invoice": "Daily Wages",
                    "method": payment.method, "amount": str(payment.amount),
                    "reference": payment.reference,
                    "status": "Reversed" if hasattr(payment, "reversal") else "Completed",
                    "category": "Staff Daily Wages",
                    "account": payment.account.name if payment.account_id else "Unlinked legacy payment",
                    "detailPath": f"/payroll/daily-wages/{payment.employee_id}",
                })

        # No matching rows are manufactured for unpaid credit purchases.
        entries.sort(key=lambda row: (row["timestamp"], row["sourceId"]), reverse=True)
        q = str(request.query_params.get("q") or "").strip().lower()[:120]
        if q:
            entries = [
                row for row in entries if any(
                    q in str(row.get(key, "")).lower()
                    for key in ("customer", "invoice", "category", "reference", "account")
                )
            ]
        direction = request.query_params.get("direction")
        if direction in ("IN", "OUT"):
            entries = [row for row in entries if row["direction"] == direction]
        try:
            page = max(1, int(request.query_params.get("page", "1")))
        except (ValueError, TypeError):
            page = 1
        try:
            page_size = max(10, min(1000, int(request.query_params.get("page_size", "50"))))
        except (ValueError, TypeError):
            page_size = 50
        return Response({
            "count": len(entries), "page": page, "pageSize": page_size,
            "results": entries[(page - 1) * page_size:page * page_size],
            "supplierPurchases": str(total_supplier_purchases.quantize(CENT)),
            "supplierPayments": str(supplier_payments.quantize(CENT)),
            "supplierOutstanding": str(supplier_outstanding.quantize(CENT)),
            "note": "Supplier credit purchases are payables until paid; paid receipts are not duplicated.",
        })
