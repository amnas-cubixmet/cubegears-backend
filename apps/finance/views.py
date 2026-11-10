"""CubeGears Finance API — dashboard, transfers, unified ledger, reports."""
from decimal import Decimal
from uuid import uuid4
import csv
from django.db import transaction
from django.db.models import Sum, Q, F, DecimalField, ExpressionWrapper
from django.http import HttpResponse
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from apps.accounts.permissions import RolePermission, is_company_owner
from apps.inventory.models import (
    WorkshopFinancialAccount, WorkshopAccountEntry, FinanceTransfer
)
from apps.payments.models import Payment
from apps.expenses.models import Expense

CENT = Decimal("0.01")


def mask_account_number(value):
    """Never transmit full bank account numbers in financial API responses."""
    cleaned = "".join(ch for ch in str(value or "") if ch.isalnum())
    return ("••••" + cleaned[-4:]) if len(cleaned) > 4 else ("••••" if cleaned else "")


def valid_money(value, field="amount", allow_zero=False):
    try:
        amount = Decimal(str(value))
        if (not amount.is_finite() or amount < 0 or
                (amount == 0 and not allow_zero) or
                amount.as_tuple().exponent < -2 or amount >= Decimal("1000000000000")):
            raise ValueError()
        return amount.quantize(CENT)
    except (ValueError, TypeError, ArithmeticError):
        raise ValidationError({field: "Enter a valid amount (up to two decimal places)."})




def can_view_finance_balances(user):
    """Require an explicit balance grant; a broad payments.view grant is insufficient."""
    if is_company_owner(user):
        return True
    role = getattr(user, "role", None)
    grants = getattr(role, "permissions", [])
    return bool(role and role.is_active and isinstance(grants, list)
                and "finance.accounts.balance.view" in grants)


def hide_finance_balances(payload):
    """Remove account balance values from serialized finance responses."""
    hidden_keys = {
        "openingBalance", "currentBalance", "bankBalance", "cashBalance",
        "fromBalance", "toBalance", "runningBalance", "newBalance",
    }
    if isinstance(payload, dict):
        return {
            key: hide_finance_balances(value)
            for key, value in payload.items()
            if key not in hidden_keys
        }
    if isinstance(payload, list):
        return [hide_finance_balances(value) for value in payload]
    return payload


class FinanceBalanceRedactionMixin:
    """Apply balance field redaction to every finance API response."""

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        if (
            response.status_code < 400
            and not can_view_finance_balances(getattr(request, "user", None))
        ):
            response.data = hide_finance_balances(response.data)
        return response


def account_balance(account):
    total = WorkshopAccountEntry.objects.filter(
        company_id=account.company_id, account=account
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
    return (account.opening_balance + total).quantize(CENT)


def accounts_summary(company_id):
    # An inactive account can still hold funds. Account deactivation must
    # block NEW postings, not make historical money disappear from balances.
    accounts = WorkshopFinancialAccount.objects.filter(company_id=company_id)
    bank_total = Decimal("0")
    cash_total = Decimal("0")
    result = []
    for acc in accounts:
        bal = account_balance(acc)
        result.append({
            "id": str(acc.pk), "name": acc.name, "kind": acc.kind,
            "bankName": acc.bank_name, "accountNumber": mask_account_number(acc.account_number),
            "ifsc": acc.ifsc, "branchName": acc.branch_name,
            "openingBalance": str(acc.opening_balance), "currentBalance": str(bal),
            "isActive": acc.is_active,
        })
        # Preserve legacy account IDs; all digital money is shown under Bank
        # in the new two-channel Finance UI, and only physical notes are Cash.
        if acc.kind in ("Bank", "Wallet", "UPI"):
            bank_total += bal
        elif acc.kind == "Cash":
            cash_total += bal
    return result, bank_total, cash_total


class FinanceDashboard(FinanceBalanceRedactionMixin, APIView):
    permission_classes = [RolePermission]
    permission_map = {"GET": ["payments.view"]}

    def get(self, request):
        from apps.inventory.models import SupplierBill
        from apps.suppliers.returns_api import bill_due
        from apps.invoices.models import Invoice, InvoiceItem
        company = request.user.company
        period = request.query_params.get("period", "all")
        if period not in ("today", "month", "all"):
            raise ValidationError({"period": "Choose today, month or all."})
        today = timezone.localdate()
        period_start = (
            today if period == "today" else
            today.replace(day=1) if period == "month" else None
        )
        period_label = {
            "today": "Today", "month": "This Month", "all": "All Time",
        }[period]
        # Financial account balances are book values: they must not reset
        # to zero when a date filter changes.
        def dated(queryset, date_field):
            return queryset.filter(**{f"{date_field}__gte": period_start}) if period_start else queryset
        accounts, bank_total, cash_total = accounts_summary(company.id)

        total_received = Payment.objects.filter(
            company=company, status="Completed", direction="IN"
        ).aggregate(v=Sum("amount"))["v"] or Decimal("0")

        # Account-based payouts must include supplier bill settlements, wages
        # and real expense payments, not just generic Payment rows. Each
        # source posts once to WorkshopAccountEntry; never sum models again.
        outgoing_types = [
            "outgoing_payment", "Supplier Payment", "Supplier Advance",
            "expense_payment", "wage_payment",
        ]
        posted_outgoing = WorkshopAccountEntry.objects.filter(
            company=company, entry_type__in=outgoing_types, amount__lt=0
        ).aggregate(v=Sum("amount"))["v"] or Decimal("0")
        reversed_outgoing = WorkshopAccountEntry.objects.filter(
            company=company,
            entry_type__in=["payment_reversal", "expense_reversal", "wage_payment_reversal"],
            amount__gt=0,
        ).aggregate(v=Sum("amount"))["v"] or Decimal("0")
        total_paid = max(Decimal("0"), -posted_outgoing - reversed_outgoing)

        outstanding_invoices = Invoice.objects.filter(
            company=company, kind="invoice"
        ).exclude(status__in=["Paid", "Cancelled"]).aggregate(v=Sum("balance"))["v"] or Decimal("0")

        # Supplier outstanding
        posted_bills = SupplierBill.objects.filter(company=company, status="Posted")
        supplier_outstanding = Decimal("0")
        for bill in posted_bills:
            supplier_outstanding += bill_due(bill)

        # Partial settlements reduce outstanding expense liabilities without
        # losing the unpaid remainder from the dashboard.
        from apps.expenses.models import ExpenseSettlement
        pending_expenses = Decimal("0")
        candidates = Expense.objects.filter(company=company).exclude(
            status__iexact="paid"
        ).exclude(status__in=["Rejected", "rejected", "Cancelled", "cancelled", "Draft", "draft"])
        for expense in candidates:
            settled = ExpenseSettlement.objects.filter(
                expense=expense, company=company, reversed_at__isnull=True,
            ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
            pending_expenses += max(Decimal("0"), expense.amount - settled)

        # Today's movement reflects real posted cash/bank entries, not
        # purchases made on supplier credit and not opening balances.
        today_operating_entries = WorkshopAccountEntry.objects.filter(
            company=company, posted_at__date=today
        ).exclude(entry_type__in=[
            "transfer_in", "transfer_out", "manual_adjustment",
        ])
        today_money_in = today_operating_entries.filter(amount__gt=0).aggregate(
            v=Sum("amount")
        )["v"] or Decimal("0")
        today_money_out = today_operating_entries.filter(amount__lt=0).aggregate(
            v=Sum("amount")
        )["v"] or Decimal("0")
        today_supplier_paid = WorkshopAccountEntry.objects.filter(
            company=company, posted_at__date=today,
            entry_type__in=["Supplier Payment", "Supplier Advance"], amount__lt=0,
        ).aggregate(v=Sum("amount"))["v"] or Decimal("0")

        # Revenue is based on posted customer INVOICES, not money received
        # (which can be an earlier invoice settlement). Exclude draft/cancelled
        # invoices and GST collected on behalf of the government.
        recognized_invoices = Invoice.objects.filter(
            company=company, kind="invoice",
            status__in=["Finalized", "Paid", "Issued", "Overdue", "Partially Paid"],
        )
        recognized_invoices = dated(recognized_invoices, "date")
        revenue_totals = recognized_invoices.aggregate(
            total=Sum("total"), cgst=Sum("cgst"), sgst=Sum("sgst"), igst=Sum("igst"),
        )
        gross_sales = revenue_totals["total"] or Decimal("0")
        sales_gst = sum(
            (revenue_totals[t] or Decimal("0") for t in ("cgst", "sgst", "igst")),
            Decimal("0"),
        )
        revenue_ex_gst = gross_sales - sales_gst

        # Operational expenses count once on the expense date, including
        # unpaid bills. Settlements do not create a second expense.
        recognized_expenses = Expense.objects.filter(company=company).exclude(
            status__in=["Draft", "Rejected", "Cancelled"]
        )
        recognized_expenses = dated(recognized_expenses, "date")
        expense_total = recognized_expenses.aggregate(v=Sum("amount"))["v"] or Decimal("0")

        # InvoiceItem purchase_price is a historical cost snapshot. This is
        # the known cost of invoiced parts, NOT the total amount purchased
        # from suppliers (unsold inventory is still an asset).
        known_cost_expression = ExpressionWrapper(
            F("quantity") * F("purchase_price"),
            output_field=DecimalField(max_digits=22, decimal_places=2),
        )
        known_cost_of_sales = InvoiceItem.objects.filter(
            invoice__in=recognized_invoices,
            purchase_price__gt=0,
        ).aggregate(v=Sum(known_cost_expression))["v"] or Decimal("0")

        estimated_profit = revenue_ex_gst - expense_total - known_cost_of_sales
        expenses_today = Expense.objects.filter(
            company=company, date=today
        ).exclude(status__in=["Draft", "Rejected", "Cancelled"]).aggregate(
            v=Sum("amount")
        )["v"] or Decimal("0")
        # This is not certified accounting profit: it does not assume
        # unrecorded supplier inventory cost or wages are zero.
        profit_note = (
            "Estimated operating result = invoiced sales excluding GST "
            "minus recorded operating expenses and known invoiced item costs. "
            "Unrecorded payroll, missing part costs, other liabilities and "
            "adjustments can change actual net profit."
        )

        recent_entries = WorkshopAccountEntry.objects.filter(
            company=company
        ).select_related("account", "created_by").order_by("-posted_at")[:10]

        recent_transactions = []
        for entry in recent_entries:
            recent_transactions.append({
                "id": str(entry.pk),
                "date": entry.posted_at.isoformat(),
                "account": entry.account.name,
                "amount": str(entry.amount),
                "credit": str(entry.amount) if entry.amount > 0 else "0.00",
                "debit": str(abs(entry.amount)) if entry.amount < 0 else "0.00",
                "type": entry.entry_type,
                "reference": entry.reference,
                "createdBy": entry.created_by.name if entry.created_by else "",
            })

        return Response({
            "bankBalance": str(bank_total.quantize(CENT)),
            "cashBalance": str(cash_total.quantize(CENT)),
            "totalReceived": str(total_received.quantize(CENT)),
            "totalPaid": str(total_paid.quantize(CENT)),
            "totalPaidBasis": "net-posted-account-outflows-excluding-internal-transfers",
            "supplierPaymentsPosted": str(abs(
                WorkshopAccountEntry.objects.filter(
                    company=company, entry_type__in=["Supplier Payment", "Supplier Advance"], amount__lt=0
                ).aggregate(v=Sum("amount"))["v"] or Decimal("0")
            ).quantize(CENT)),
            "outstandingCustomer": str(outstanding_invoices.quantize(CENT)),
            "supplierOutstanding": str(supplier_outstanding.quantize(CENT)),
            "pendingExpenses": str(pending_expenses.quantize(CENT)),
            "accounts": accounts,
            "recentTransactions": recent_transactions,
            "period": period,
            "periodLabel": period_label,
            "asOfDate": today.isoformat(),
            "todayReceived": str(today_money_in.quantize(CENT)),
            "todayPaid": str(abs(today_money_out).quantize(CENT)),
            "todaySupplierPaid": str(abs(today_supplier_paid).quantize(CENT)),
            "todayExpensesRecorded": str(expenses_today.quantize(CENT)),
            "netSalesRevenue": str(revenue_ex_gst.quantize(CENT)),
            "salesTaxExcluded": str(sales_gst.quantize(CENT)),
            "recordedExpenses": str(expense_total.quantize(CENT)),
            "knownCostOfSales": str(known_cost_of_sales.quantize(CENT)),
            "estimatedProfit": str(estimated_profit.quantize(CENT)),
            "profitNote": profit_note,
            "cashFlowNote": "Today money in/out follows posted account ledger entries (excluding transfers and opening/adjustment entries).",
            "accountBalanceNote": "Internal bookkeeping balances, not live bank balances.",
        })


class FinanceAccountCollection(FinanceBalanceRedactionMixin, APIView):
    permission_classes = [RolePermission]
    permission_map = {"GET": ["payments.view"], "POST": ["payments.edit"]}

    def get(self, request):
        accounts = WorkshopFinancialAccount.objects.filter(company=request.user.company)
        include_inactive = request.query_params.get("include_inactive") == "1"
        if not include_inactive:
            accounts = accounts.filter(is_active=True)
        result = []
        for acc in accounts:
            bal = account_balance(acc)
            result.append({
                "id": str(acc.pk), "name": acc.name, "kind": acc.kind,
                "bankName": acc.bank_name, "accountNumber": mask_account_number(acc.account_number),
                "ifsc": acc.ifsc, "branchName": acc.branch_name,
                "openingBalance": str(acc.opening_balance),
                "openingDate": acc.opening_date.isoformat() if acc.opening_date else None,
                "currentBalance": str(bal), "isActive": acc.is_active,
                "description": acc.description,
            })
        return Response(result)

    @transaction.atomic
    def post(self, request):
        d = request.data
        name = str(d.get("name", "")).strip()
        if not name:
            raise ValidationError({"name": "Account name is required."})
        kind = d.get("kind", "Cash")
        if kind not in ("Cash", "Bank", "UPI", "Wallet", "Other"):
            raise ValidationError({"kind": "Choose Cash, Bank, UPI, Wallet or Other."})
        opening = valid_money(d.get("openingBalance", "0"), "openingBalance", allow_zero=True)
        if WorkshopFinancialAccount.objects.filter(company=request.user.company, name=name).exists():
            raise ValidationError({"name": "An account with this name already exists."})
        acc = WorkshopFinancialAccount.objects.create(
            company=request.user.company, branch=request.user.branch,
            name=name, kind=kind, opening_balance=opening,
            bank_name=str(d.get("bankName", "") or ""),
            account_number=str(d.get("accountNumber", "") or ""),
            ifsc=str(d.get("ifsc", "") or ""),
            branch_name=str(d.get("branchName", "") or ""),
            opening_date=d.get("openingDate") or None,
            description=str(d.get("description", "") or ""),
            is_active=True, created_by=request.user,
        )
        return Response({"id": str(acc.pk), "name": acc.name, "kind": acc.kind,
                         "currentBalance": str(acc.opening_balance), "isActive": True}, status=201)


class FinanceAccountDetail(FinanceBalanceRedactionMixin, APIView):
    permission_classes = [RolePermission]
    permission_map = {"GET": ["payments.view"], "PATCH": ["payments.edit"]}

    def _get_account(self, request, pk):
        from django.shortcuts import get_object_or_404
        return get_object_or_404(WorkshopFinancialAccount, pk=pk, company=request.user.company)

    def get(self, request, pk):
        acc = self._get_account(request, pk)
        bal = account_balance(acc)
        entries = list(WorkshopAccountEntry.objects.filter(
            company=request.user.company, account=acc
        ).select_related("created_by").order_by("-posted_at", "-created_at", "-pk")[:200])
        ledger = []
        # Start from the balance just before this newest-page slice; opening
        # balance alone is incorrect once an account has more than 200 entries.
        running = bal - sum((item.amount for item in entries), Decimal("0"))
        for entry in reversed(entries):
            running += entry.amount
            ledger.append({
                "id": str(entry.pk), "date": entry.posted_at.isoformat(),
                "type": entry.entry_type, "amount": str(entry.amount),
                "credit": str(entry.amount) if entry.amount > 0 else "0.00",
                "debit": str(abs(entry.amount)) if entry.amount < 0 else "0.00",
                "runningBalance": str(running.quantize(CENT)),
                "reference": entry.reference,
                "sourceKey": entry.source_key,
                "createdBy": entry.created_by.name if entry.created_by else "",
            })
        return Response({
            "id": str(acc.pk), "name": acc.name, "kind": acc.kind,
            "bankName": acc.bank_name, "accountNumber": mask_account_number(acc.account_number),
            "ifsc": acc.ifsc, "branchName": acc.branch_name,
            "openingBalance": str(acc.opening_balance), "currentBalance": str(bal),
            "isActive": acc.is_active, "description": acc.description,
            "openingDate": acc.opening_date.isoformat() if acc.opening_date else None,
            "ledger": list(reversed(ledger)),
        })

    @transaction.atomic
    def patch(self, request, pk):
        acc = WorkshopFinancialAccount.objects.select_for_update().get(
            pk=pk, company=request.user.company
        )
        d = request.data
        if "name" in d:
            new_name = str(d["name"]).strip()
            if not new_name:
                raise ValidationError({"name": "Name cannot be blank."})
            if WorkshopFinancialAccount.objects.filter(
                company=request.user.company, name=new_name
            ).exclude(pk=pk).exists():
                raise ValidationError({"name": "Name already in use."})
            acc.name = new_name
        for field in ("bankName", "accountNumber", "ifsc", "branchName", "description"):
            model_field = {"bankName": "bank_name", "accountNumber": "account_number",
                           "ifsc": "ifsc", "branchName": "branch_name",
                           "description": "description"}[field]
            if field in d:
                value = str(d[field] or "")
                # The UI receives masked numbers. Saving unchanged metadata
                # must never overwrite the stored number with its mask.
                if field == "accountNumber" and "•" in value:
                    continue
                setattr(acc, model_field, value)
        if "openingDate" in d:
            if WorkshopAccountEntry.objects.filter(company=request.user.company, account=acc).exists():
                raise ValidationError({"openingDate": "Posted account dates are immutable; use an audited adjustment."})
            acc.opening_date = d["openingDate"] or None
        if "isActive" in d:
            if not isinstance(d["isActive"], bool):
                raise ValidationError({"isActive": "Expected true or false."})
            acc.is_active = d["isActive"]
        acc.save()
        return Response({"id": str(acc.pk), "name": acc.name, "isActive": acc.is_active})


class FinanceAdjustment(FinanceBalanceRedactionMixin, APIView):
    """Create an auditable manual balance adjustment entry."""
    permission_classes = [RolePermission]
    permission_map = {"POST": ["payments.edit"]}

    @transaction.atomic
    def post(self, request, pk):
        from django.shortcuts import get_object_or_404
        acc = get_object_or_404(
            WorkshopFinancialAccount.objects.select_for_update(),
            pk=pk, company=request.user.company
        )
        d = request.data
        amount = valid_money(d.get("amount"), "amount")
        direction = d.get("direction", "credit")
        if direction not in ("credit", "debit"):
            raise ValidationError({"direction": "Must be 'credit' or 'debit'."})
        signed = amount if direction == "credit" else -amount
        if not acc.is_active:
            raise ValidationError({"account": "Cannot adjust an inactive account."})
        if signed < 0 and account_balance(acc) < amount:
            raise ValidationError({"amount": "Insufficient funds for this adjustment."})
        reason = str(d.get("reason", "")).strip()
        if not reason:
            raise ValidationError({"reason": "An audit reason is required for balance adjustments."})
        source_key = f"adj_{acc.pk}_{uuid4().hex}"
        WorkshopAccountEntry.objects.create(
            company=request.user.company, branch=request.user.branch,
            account=acc, amount=signed,
            entry_type="manual_adjustment",
            source_key=source_key,
            reference=reason[:120],
            posted_at=timezone.now(),
            created_by=request.user,
        )
        return Response({"newBalance": str(account_balance(acc)), "adjustment": str(signed)}, status=201)


class FinanceTransferCollection(FinanceBalanceRedactionMixin, APIView):
    permission_classes = [RolePermission]
    permission_map = {"GET": ["payments.view"], "POST": ["payments.edit"]}

    def get(self, request):
        transfers = FinanceTransfer.objects.filter(
            company=request.user.company
        ).select_related("from_account", "to_account", "created_by").order_by("-transfer_date")[:100]
        result = []
        for t in transfers:
            result.append({
                "id": str(t.pk), "fromAccount": t.from_account.name,
                "fromAccountId": str(t.from_account_id),
                "toAccount": t.to_account.name, "toAccountId": str(t.to_account_id),
                "amount": str(t.amount), "date": t.transfer_date.isoformat(),
                "reference": t.reference, "notes": t.notes,
                "createdBy": t.created_by.name if t.created_by else "",
                "createdAt": t.created_at.isoformat(),
            })
        return Response(result)

    @transaction.atomic
    def post(self, request):
        from django.shortcuts import get_object_or_404
        d = request.data
        amount = valid_money(d.get("amount"), "amount")
        if amount <= 0:
            raise ValidationError({"amount": "Transfer amount must be positive."})
        from_id = d.get("fromAccountId")
        to_id = d.get("toAccountId")
        if not from_id or not to_id:
            raise ValidationError({"fromAccountId": "Both accounts are required."})
        if str(from_id) == str(to_id):
            raise ValidationError({"toAccountId": "Cannot transfer to the same account."})
        from_acc = get_object_or_404(
            WorkshopFinancialAccount.objects.select_for_update(),
            pk=from_id, company=request.user.company, is_active=True
        )
        to_acc = get_object_or_404(
            WorkshopFinancialAccount.objects.select_for_update(),
            pk=to_id, company=request.user.company, is_active=True
        )
        # Check sufficient balance
        from_balance = account_balance(from_acc)
        if from_balance < amount:
            raise ValidationError({"amount": f"Insufficient balance in {from_acc.name}. Available: {from_balance}"})

        idempotency_key = str(d.get("idempotencyKey") or uuid4().hex).strip()
        if not idempotency_key or len(idempotency_key) > 160:
            raise ValidationError({"idempotencyKey": "Invalid transfer request key."})
        if FinanceTransfer.objects.filter(company=request.user.company, idempotency_key=idempotency_key).exists():
            raise ValidationError({"idempotencyKey": "Duplicate transfer request."})

        import datetime
        transfer_date_raw = d.get("date") or timezone.localdate().isoformat()
        try:
            transfer_date = datetime.date.fromisoformat(str(transfer_date_raw))
        except ValueError:
            raise ValidationError({"date": "Enter a valid date."})

        transfer = FinanceTransfer.objects.create(
            company=request.user.company, branch=request.user.branch,
            from_account=from_acc, to_account=to_acc,
            amount=amount, transfer_date=transfer_date,
            reference=str(d.get("reference", "") or "")[:120],
            notes=str(d.get("notes", "") or ""),
            idempotency_key=idempotency_key,
            created_by=request.user,
        )
        now = timezone.now()
        source_base = f"transfer_{transfer.pk}"
        WorkshopAccountEntry.objects.create(
            company=request.user.company, branch=request.user.branch,
            account=from_acc, amount=-amount,
            entry_type="transfer_out",
            source_key=f"{source_base}_out",
            reference=f"Transfer to {to_acc.name}" + (f" | {transfer.reference}" if transfer.reference else ""),
            posted_at=now, created_by=request.user,
        )
        WorkshopAccountEntry.objects.create(
            company=request.user.company, branch=request.user.branch,
            account=to_acc, amount=amount,
            entry_type="transfer_in",
            source_key=f"{source_base}_in",
            reference=f"Transfer from {from_acc.name}" + (f" | {transfer.reference}" if transfer.reference else ""),
            posted_at=now, created_by=request.user,
        )
        return Response({
            "id": str(transfer.pk), "amount": str(amount),
            "fromAccount": from_acc.name, "toAccount": to_acc.name,
            "fromBalance": str(account_balance(from_acc)),
            "toBalance": str(account_balance(to_acc)),
        }, status=201)


class FinanceLedger(FinanceBalanceRedactionMixin, APIView):
    """Unified transaction ledger across all accounts with running balance."""
    permission_classes = [RolePermission]
    permission_map = {"GET": ["payments.view"]}

    def get(self, request):
        company = request.user.company
        qs = WorkshopAccountEntry.objects.filter(company=company).select_related(
            "account", "created_by"
        )
        # Filters
        account_id = request.query_params.get("account")
        if account_id:
            qs = qs.filter(account_id=account_id)
        entry_type = request.query_params.get("type")
        if entry_type:
            qs = qs.filter(entry_type=entry_type)
        date_from = request.query_params.get("from")
        if date_from:
            qs = qs.filter(posted_at__date__gte=date_from)
        date_to = request.query_params.get("to")
        if date_to:
            qs = qs.filter(posted_at__date__lte=date_to)
        search = request.query_params.get("q", "").strip()
        if search:
            qs = qs.filter(Q(reference__icontains=search) | Q(source_key__icontains=search))

        # CSV export
        if request.query_params.get("export") == "csv":
            return self._csv_export(qs)
        if request.query_params.get("export") == "pdf":
            return self._pdf_export(qs)

        # Pagination
        try:
            page = max(1, int(request.query_params.get("page", 1)))
            page_size = min(200, max(10, int(request.query_params.get("page_size", 50))))
        except (ValueError, TypeError):
            page, page_size = 1, 50

        total_count = qs.count()
        entries = list(qs.order_by("-posted_at", "-created_at")[(page-1)*page_size:page*page_size])

        result = []
        for entry in entries:
            result.append({
                "id": str(entry.pk),
                "date": entry.posted_at.date().isoformat(),
                "accountId": str(entry.account_id),
                "account": entry.account.name,
                "accountKind": entry.account.kind,
                "credit": str(entry.amount.quantize(CENT)) if entry.amount > 0 else "0.00",
                "debit": str(abs(entry.amount).quantize(CENT)) if entry.amount < 0 else "0.00",
                "amount": str(entry.amount.quantize(CENT)),
                "type": entry.entry_type,
                "reference": entry.reference,
                "sourceKey": entry.source_key,
                "createdBy": entry.created_by.name if entry.created_by else "",
                "createdAt": entry.created_at.isoformat(),
            })

        return Response({
            "count": total_count,
            "page": page,
            "pageSize": page_size,
            "totalPages": (total_count + page_size - 1) // page_size,
            "results": result,
        })

    def _pdf_export(self, qs):
        """Authenticated, capped account ledger PDF export."""
        from io import BytesIO
        from xml.sax.saxutils import escape
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle

        buf = BytesIO()
        doc = SimpleDocTemplate(
            buf, pagesize=landscape(A4), leftMargin=24,
            rightMargin=24, topMargin=24, bottomMargin=24,
        )
        normal = getSampleStyleSheet()["BodyText"]
        normal.fontSize = 7
        normal.leading = 9
        headers = ["Date", "Account", "Category", "Credit", "Debit", "Reference", "Posted by"]
        rows = [headers]
        for entry in qs.select_related("account", "created_by").order_by("-posted_at", "-pk")[:1000]:
            def cell(value):
                return Paragraph(escape(str(value or "")[:110]), normal)
            rows.append([
                cell(entry.posted_at.date().isoformat()),
                cell(entry.account.name),
                cell(entry.entry_type),
                cell(str(entry.amount) if entry.amount > 0 else "0.00"),
                cell(str(-entry.amount) if entry.amount < 0 else "0.00"),
                cell(entry.reference),
                cell(entry.created_by.name if entry.created_by else ""),
            ])
        table = Table(rows, repeatRows=1, colWidths=[65, 110, 95, 64, 64, 230, 105])
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e7edf5")),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d2dae4")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        doc.build([
            Paragraph("CubeGears - Internal Account Transaction Ledger", getSampleStyleSheet()["Heading2"]),
            Paragraph("Bookkeeping balances only; not live bank statements. Up to 1,000 matching entries.", normal),
            table,
        ])
        result = HttpResponse(buf.getvalue(), content_type="application/pdf")
        result["Content-Disposition"] = 'attachment; filename="transactions.pdf"'
        return result

    def _csv_export(self, qs):
        # Never let user supplied account/ref text execute as spreadsheet formulas.
        def csv_safe(value):
            text = str(value or "")
            return "'" + text if text.startswith(("=", "+", "-", "@", "\t", "\r", "\n")) else text

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="transactions.csv"'
        writer = csv.writer(response)
        writer.writerow(["Date", "Account", "Type", "Credit", "Debit", "Reference", "Source Key", "Created By"])
        for entry in qs.order_by("-posted_at")[:5000]:
            credit = str(entry.amount) if entry.amount > 0 else "0.00"
            debit = str(abs(entry.amount)) if entry.amount < 0 else "0.00"
            writer.writerow([
                entry.posted_at.date().isoformat(), csv_safe(entry.account.name),
                csv_safe(entry.entry_type), credit, debit, csv_safe(entry.reference),
                csv_safe(entry.source_key), csv_safe(entry.created_by.name if entry.created_by else ""),
            ])
        return response


class FinanceReports(FinanceBalanceRedactionMixin, APIView):
    permission_classes = [RolePermission]
    permission_map = {"GET": ["payments.view"]}

    def get(self, request):
        company = request.user.company
        date_from = request.query_params.get("from")
        date_to = request.query_params.get("to")
        qs = WorkshopAccountEntry.objects.filter(company=company)
        if date_from:
            qs = qs.filter(posted_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(posted_at__date__lte=date_to)

        # Separate book transfers and equity adjustments from operating flows.
        operating = qs.exclude(entry_type__in=["transfer_in", "transfer_out", "manual_adjustment"])
        total_credits = operating.filter(amount__gt=0).aggregate(v=Sum("amount"))["v"] or Decimal("0")
        total_debits = operating.filter(amount__lt=0).aggregate(v=Sum("amount"))["v"] or Decimal("0")
        transfer_volume = qs.filter(entry_type="transfer_out").aggregate(v=Sum("amount"))["v"] or Decimal("0")

        by_type = {}
        for row in qs.values("entry_type").annotate(total=Sum("amount")):
            by_type[row["entry_type"]] = str(row["total"].quantize(CENT))

        accounts, bank_total, cash_total = accounts_summary(company.id)

        return Response({
            "totalCredits": str(total_credits.quantize(CENT)),
            "totalDebits": str(abs(total_debits).quantize(CENT)),
            "netFlow": str((total_credits + total_debits).quantize(CENT)),
            "internalTransfers": str(abs(transfer_volume).quantize(CENT)),
            "bankBalance": str(bank_total.quantize(CENT)),
            "cashBalance": str(cash_total.quantize(CENT)),
            "byType": by_type,
            "accounts": accounts,
        })
