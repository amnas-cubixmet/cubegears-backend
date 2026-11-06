"""CubeGears Finance API — dashboard, transfers, unified ledger, reports."""
from decimal import Decimal
from uuid import uuid4
import csv
from django.db import transaction
from django.db.models import Sum, Q
from django.http import HttpResponse
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from apps.accounts.permissions import RolePermission
from apps.inventory.models import (
    WorkshopFinancialAccount, WorkshopAccountEntry, FinanceTransfer
)
from apps.payments.models import Payment
from apps.expenses.models import Expense

CENT = Decimal("0.01")


def account_balance(account):
    total = WorkshopAccountEntry.objects.filter(
        company_id=account.company_id, account=account
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
    return (account.opening_balance + total).quantize(CENT)


def accounts_summary(company_id):
    accounts = WorkshopFinancialAccount.objects.filter(company_id=company_id, is_active=True)
    bank_total = Decimal("0")
    cash_total = Decimal("0")
    result = []
    for acc in accounts:
        bal = account_balance(acc)
        result.append({
            "id": str(acc.pk), "name": acc.name, "kind": acc.kind,
            "bankName": acc.bank_name, "accountNumber": acc.account_number,
            "ifsc": acc.ifsc, "branchName": acc.branch_name,
            "openingBalance": str(acc.opening_balance), "currentBalance": str(bal),
            "isActive": acc.is_active,
        })
        if acc.kind == "Bank":
            bank_total += bal
        elif acc.kind in ("Cash", "Wallet", "UPI"):
            cash_total += bal
    return result, bank_total, cash_total


class FinanceDashboard(APIView):
    permission_classes = [RolePermission]
    permission_map = {"GET": ["payments.view"]}

    def get(self, request):
        from apps.inventory.models import SupplierBill, SupplierPaymentAllocation
        company = request.user.company
        accounts, bank_total, cash_total = accounts_summary(company.id)

        total_received = Payment.objects.filter(
            company=company, status="Completed", direction="IN"
        ).aggregate(v=Sum("amount"))["v"] or Decimal("0")

        total_paid = Payment.objects.filter(
            company=company, status="Completed", direction="OUT"
        ).aggregate(v=Sum("amount"))["v"] or Decimal("0")

        from apps.invoices.models import Invoice
        outstanding_invoices = Invoice.objects.filter(
            company=company, kind="invoice"
        ).exclude(status__in=["Paid", "Cancelled"]).aggregate(v=Sum("balance"))["v"] or Decimal("0")

        # Supplier outstanding
        posted_bills = SupplierBill.objects.filter(company=company, status="Posted")
        supplier_outstanding = Decimal("0")
        for bill in posted_bills:
            paid = SupplierPaymentAllocation.objects.filter(
                company_id=company.id, bill=bill
            ).aggregate(v=Sum("amount"))["v"] or Decimal("0")
            supplier_outstanding += max(Decimal("0"), bill.total - paid)

        pending_expenses = Expense.objects.filter(
            company=company, status__in=["Pending", "Unpaid", "pending", "unpaid"]
        ).aggregate(v=Sum("amount"))["v"] or Decimal("0")

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
            "outstandingCustomer": str(outstanding_invoices.quantize(CENT)),
            "supplierOutstanding": str(supplier_outstanding.quantize(CENT)),
            "pendingExpenses": str(pending_expenses.quantize(CENT)),
            "accounts": accounts,
            "recentTransactions": recent_transactions,
        })


class FinanceAccountCollection(APIView):
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
                "bankName": acc.bank_name, "accountNumber": acc.account_number,
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
        if kind not in ("Cash", "Bank", "UPI", "Wallet"):
            raise ValidationError({"kind": "Choose Cash, Bank, UPI or Wallet."})
        try:
            opening = Decimal(str(d.get("openingBalance", "0"))).quantize(CENT)
        except Exception:
            raise ValidationError({"openingBalance": "Enter a valid amount."})
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


class FinanceAccountDetail(APIView):
    permission_classes = [RolePermission]
    permission_map = {"GET": ["payments.view"], "PATCH": ["payments.edit"]}

    def _get_account(self, request, pk):
        from django.shortcuts import get_object_or_404
        return get_object_or_404(WorkshopFinancialAccount, pk=pk, company=request.user.company)

    def get(self, request, pk):
        acc = self._get_account(request, pk)
        bal = account_balance(acc)
        entries = WorkshopAccountEntry.objects.filter(
            company=request.user.company, account=acc
        ).select_related("created_by").order_by("-posted_at")[:200]
        ledger = []
        running = acc.opening_balance
        for entry in reversed(list(entries)):
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
            "bankName": acc.bank_name, "accountNumber": acc.account_number,
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
                setattr(acc, model_field, str(d[field] or ""))
        if "openingDate" in d:
            acc.opening_date = d["openingDate"] or None
        if "isActive" in d:
            acc.is_active = bool(d["isActive"])
        acc.save()
        return Response({"id": str(acc.pk), "name": acc.name, "isActive": acc.is_active})


class FinanceAdjustment(APIView):
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
        try:
            amount = Decimal(str(d.get("amount", "0"))).quantize(CENT)
        except Exception:
            raise ValidationError({"amount": "Enter a valid decimal amount."})
        direction = d.get("direction", "credit")
        if direction not in ("credit", "debit"):
            raise ValidationError({"direction": "Must be 'credit' or 'debit'."})
        signed = amount if direction == "credit" else -amount
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


class FinanceTransferCollection(APIView):
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
        try:
            amount = Decimal(str(d.get("amount", "0"))).quantize(CENT)
        except Exception:
            raise ValidationError({"amount": "Enter a valid decimal amount."})
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

        idempotency_key = str(d.get("idempotencyKey") or uuid4().hex)
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


class FinanceLedger(APIView):
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

    def _csv_export(self, qs):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="transactions.csv"'
        writer = csv.writer(response)
        writer.writerow(["Date", "Account", "Type", "Credit", "Debit", "Reference", "Source Key", "Created By"])
        for entry in qs.order_by("-posted_at")[:5000]:
            credit = str(entry.amount) if entry.amount > 0 else "0.00"
            debit = str(abs(entry.amount)) if entry.amount < 0 else "0.00"
            writer.writerow([
                entry.posted_at.date().isoformat(), entry.account.name,
                entry.entry_type, credit, debit, entry.reference,
                entry.source_key, entry.created_by.name if entry.created_by else "",
            ])
        return response


class FinanceReports(APIView):
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

        total_credits = qs.filter(amount__gt=0).aggregate(v=Sum("amount"))["v"] or Decimal("0")
        total_debits = qs.filter(amount__lt=0).aggregate(v=Sum("amount"))["v"] or Decimal("0")

        by_type = {}
        for row in qs.values("entry_type").annotate(total=Sum("amount")):
            by_type[row["entry_type"]] = str(row["total"].quantize(CENT))

        accounts, bank_total, cash_total = accounts_summary(company.id)

        return Response({
            "totalCredits": str(total_credits.quantize(CENT)),
            "totalDebits": str(abs(total_debits).quantize(CENT)),
            "netFlow": str((total_credits + total_debits).quantize(CENT)),
            "bankBalance": str(bank_total.quantize(CENT)),
            "cashBalance": str(cash_total.quantize(CENT)),
            "byType": by_type,
            "accounts": accounts,
        })
