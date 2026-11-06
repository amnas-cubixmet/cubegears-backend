"""Read-only, tenant-scoped payments home: cash flow != earned profit."""
from datetime import date
from decimal import Decimal

from django.db.models import DecimalField, ExpressionWrapper, F, Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import RolePermission, user_has_permission
from apps.expenses.models import Expense
from apps.invoices.models import Invoice, InvoiceItem
from apps.inventory.models import WorkshopAccountEntry, WorkshopFinancialAccount
from .views import account_balance

ZERO = Decimal("0.00")
CENT = Decimal("0.01")

# Book transfers and adjustments are not income or expenses.
INCOMING = ("customer_payment",)
OUTGOING = ("outgoing_payment", "Supplier Payment", "Supplier Advance", "expense_payment", "wage_payment")
RECEIPT_REVERSALS = ("payment_reversal",)
OUTFLOW_REVERSALS = ("expense_reversal", "wage_payment_reversal")
DUPLICATE_SUPPLIER_EXPENSE_CATEGORIES = (
    "supplier payment", "supplier payments", "spare parts",
    "spare part purchase", "stock purchase", "inventory purchase",
)


def total_amount(qs):
    return qs.aggregate(value=Sum("amount"))["value"] or ZERO


class PaymentsHomeOverview(APIView):
    """Overview for /payments. Never posts, mutates or guesses bank funds."""
    permission_classes = [RolePermission]
    permission_map = {"GET": "payments.view"}

    def get(self, request):
        company = request.user.company
        selected = request.query_params.get("date")
        try:
            day = date.fromisoformat(selected) if selected else timezone.localdate()
        except (TypeError, ValueError):
            raise ValidationError({"date": "Date must be YYYY-MM-DD."})

        ledger = WorkshopAccountEntry.objects.filter(company=company)
        day_entries = ledger.filter(posted_at__date=day)
        incoming = day_entries.filter(amount__gt=0, entry_type__in=INCOMING)
        outgoing = day_entries.filter(amount__lt=0, entry_type__in=OUTGOING)
        today_received = total_amount(incoming)
        today_paid = abs(total_amount(outgoing))
        # Reversals are displayed separately, not falsely called new collections.
        today_reversals = total_amount(day_entries.filter(
            entry_type__in=RECEIPT_REVERSALS + OUTFLOW_REVERSALS
        ))

        accounts = []
        totals = {kind: ZERO for kind in ("Bank", "Cash", "UPI", "Wallet", "Other")}
        for account in WorkshopFinancialAccount.objects.filter(company=company).order_by("name"):
            balance = account_balance(account)
            accounts.append({
                "id": str(account.pk), "name": account.name,
                "kind": account.kind, "balance": str(balance),
                "openingBalance": str(account.opening_balance),
                "isActive": account.is_active,
            })
            totals[account.kind] = totals.get(account.kind, ZERO) + balance

        summary = {
            "date": day.isoformat(),
            "todayReceived": str(today_received.quantize(CENT)),
            "todayPaid": str(today_paid.quantize(CENT)),
            "todayNetCashFlow": str((today_received - today_paid + today_reversals).quantize(CENT)),
            "todayReversalNet": str(today_reversals.quantize(CENT)),
            "bankBalance": str(totals["Bank"].quantize(CENT)),
            "cashBalance": str(totals["Cash"].quantize(CENT)),
            "upiBalance": str(totals["UPI"].quantize(CENT)),
            "otherBalance": str((totals["Wallet"] + totals["Other"]).quantize(CENT)),
            "totalBookBalance": str(sum(totals.values(), ZERO).quantize(CENT)),
            "accounts": accounts,
            "accountingNote": "Balances reflect internal workshop ledger, not live bank feeds.",
        }

        # Profitability is not the cash collected or paid to a supplier.
        # Do not disclose accounting totals through payments.view alone.
        can_sales = user_has_permission(request.user, "invoices.view")
        can_expenses = user_has_permission(request.user, "expenses.view")
        if can_sales and can_expenses:
            invoices = Invoice.objects.filter(company=company, kind="invoice").exclude(
                status__iexact="draft"
            ).exclude(status__iexact="cancelled").exclude(status__iexact="void")
            billed = invoices.aggregate(
                gross=Sum("total"), cgst=Sum("cgst"), sgst=Sum("sgst"), igst=Sum("igst"),
            )
            revenue = (
                (billed["gross"] or ZERO) - (billed["cgst"] or ZERO)
                - (billed["sgst"] or ZERO) - (billed["igst"] or ZERO)
            )
            cost_expression = ExpressionWrapper(
                F("quantity") * F("purchase_price"),
                output_field=DecimalField(max_digits=24, decimal_places=2),
            )
            billed_items = InvoiceItem.objects.filter(invoice__in=invoices)
            cogs = billed_items.aggregate(value=Sum(cost_expression))["value"] or ZERO
            unknown_stock_costs = billed_items.filter(
                stock_item__isnull=False, purchase_price__lte=0
            ).count()
            expenses = Expense.objects.filter(company=company).exclude(
                status__iexact="rejected"
            ).exclude(status__iexact="cancelled").exclude(
                status__iexact="draft"
            ).exclude(category__iexact="supplier payment")
            for category in DUPLICATE_SUPPLIER_EXPENSE_CATEGORIES:
                expenses = expenses.exclude(category__iexact=category)
            operating = expenses.aggregate(value=Sum("amount"))["value"] or ZERO
            cost = operating + cogs
            summary["performance"] = {
                "revenue": str(revenue.quantize(CENT)),
                "operatingExpenses": str(operating.quantize(CENT)),
                "costOfPartsSold": str(cogs.quantize(CENT)),
                "totalKnownExpenses": str(cost.quantize(CENT)),
                "estimatedProfit": str((revenue - cost).quantize(CENT)),
                "isEstimate": True,
                "missingStockCosts": unknown_stock_costs,
                "note": "Estimate based on issued invoices ex-GST, recorded stock purchase costs on sold invoice items and workshop expense liabilities. Payroll/other unrecorded costs may be missing; supplier purchases and credit repayments are not expensed again.",
            }
        else:
            summary["performance"] = None
            summary["performanceAccessNote"] = "Invoice and Expense view permissions are required to display revenue and estimated profit."

        return Response(summary)
