"""Financial statements and bank statement reconciliation from balanced journal lines."""
from datetime import date
from decimal import Decimal
import re

from django.db import transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import RolePermission
from apps.companies.models import Company
from apps.inventory.models import (
    SupplierBill, WorkshopAccountEntry, WorkshopFinancialAccount,
)
from .ledger_engine import CHART, account_for, post_journal
from .models import (
    BankReconciliation, BankReconciliationLine, JournalEntry, JournalLine,
    LedgerAccount,
)
from .views import can_view_finance_balances

ZERO = Decimal("0.00")
CENT = Decimal("0.01")


def guard_balances(request):
    """A payment-entry role must not infer private bank balances via reports."""
    if not can_view_finance_balances(request.user):
        raise PermissionDenied("Finance account balance permission is required.")


def iso_date(value, name, optional=False):
    if optional and not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except (ValueError, TypeError):
        raise ValidationError({name: "Use a valid YYYY-MM-DD date."})


def positive_money(value, name="amount", allow_zero=False):
    try:
        amount = Decimal(str(value))
        if (not amount.is_finite() or amount < ZERO
                or (not allow_zero and amount == ZERO)
                or amount.as_tuple().exponent < -2
                or amount >= Decimal("1000000000000")):
            raise ValueError
        return amount.quantize(CENT)
    except (ValueError, TypeError, ArithmeticError):
        raise ValidationError({name: "Use a valid positive value with two decimals."})


def request_token(data):
    key = str(data.get("requestKey") or data.get("idempotencyKey") or "").strip()
    if not (8 <= len(key) <= 120 and re.fullmatch(r"[A-Za-z0-9_.:-]+", key)):
        raise ValidationError({"requestKey": "Unique request key must be 8–120 ASCII characters."})
    return key


def journal_json(journal):
    return {
        "id": str(journal.pk), "sourceKey": journal.source_key,
        "postedAt": journal.posted_at.isoformat(), "reference": journal.reference,
        "memo": journal.memo, "kind": journal.entry_kind,
        "lines": [{
            "code": line.account.code, "account": line.account.name,
            "debit": str(line.debit), "credit": str(line.credit),
        } for line in journal.lines.all()],
    }


def report_rows(company, date_from=None, date_to=None):
    qs = JournalLine.objects.filter(
        journal__company=company,
    ).select_related("account", "journal")
    if date_from:
        qs = qs.filter(journal__posted_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(journal__posted_at__date__lte=date_to)
    totals = {}
    for line in qs:
        key = str(line.account_id)
        row = totals.setdefault(key, {
            "code": line.account.code, "name": line.account.name,
            "type": line.account.account_type,
            "debit": ZERO, "credit": ZERO,
        })
        row["debit"] += line.debit
        row["credit"] += line.credit
    return sorted(totals.values(), key=lambda row: row["code"])


def coverage(company):
    """Do not claim a historically complete statement until backfill runs."""
    company_id = company.pk
    source_keys = set(JournalEntry.objects.filter(
        company=company,
    ).values_list("source_key", flat=True))
    cash_missing = sum(
        1 for pk in WorkshopAccountEntry.objects.filter(
            company=company,
        ).values_list("pk", flat=True)
        if f"cash:{pk}" not in source_keys
    )
    opening_missing = sum(
        1 for pk in WorkshopFinancialAccount.objects.filter(
            company=company, opening_balance__gt=0,
        ).values_list("pk", flat=True)
        if f"opening:{pk}" not in source_keys
    )
    bill_missing = sum(
        1 for pk in SupplierBill.objects.filter(
            company=company, status="Posted", total__gt=0,
        ).values_list("pk", flat=True)
        if f"supplier-bill:{pk}" not in source_keys
    )
    from apps.invoices.models import Invoice
    invoice_missing = sum(
        1 for pk in Invoice.objects.filter(
            company=company, kind="invoice",
            finalized_at__isnull=False, total__gt=0,
        ).exclude(status="Cancelled").values_list("pk", flat=True)
        if f"invoice:{pk}" not in source_keys
    )
    from apps.expenses.models import Expense
    expense_missing = sum(
        1 for pk in Expense.objects.filter(
            company=company, amount__gt=0,
        ).exclude(status__iexact="draft").exclude(
            status__iexact="rejected"
        ).exclude(status__iexact="cancelled").values_list("pk", flat=True)
        if not any(k.startswith(f"expense:{pk}:") for k in source_keys)
    )
    missing = cash_missing + opening_missing + bill_missing + invoice_missing + expense_missing
    unclassified = JournalLine.objects.filter(
        journal__company_id=company_id, account__code="9999",
    ).count()
    return {
        "isComplete": missing == 0 and unclassified == 0,
        "missingSourceCount": missing, "unclassifiedLineCount": unclassified,
        "missing": {
            "cash": cash_missing, "opening": opening_missing,
            "supplierBills": bill_missing, "invoices": invoice_missing,
            "expenses": expense_missing,
        },
        "warning": (
            "Historical or unclassified transactions remain. Run audited journal backfill "
            "and reconcile suspense before relying on these statements."
            if missing or unclassified else None
        ),
    }


class ChartOfAccounts(APIView):
    permission_classes = [RolePermission]
    permission_map = {"GET": ["payments.view"]}

    def get(self, request):
        guard_balances(request)
        company = request.user.company
        for code in CHART:
            account_for(company.pk, code)
        for cash in WorkshopFinancialAccount.objects.filter(company=company):
            account_for(company.pk, None, cash)
        accounts = LedgerAccount.objects.filter(company=company).order_by("code")
        return Response([{
            "id": str(row.pk), "code": row.code, "name": row.name,
            "type": row.account_type, "isActive": row.is_active,
            "financialAccountId": str(row.financial_account_id)
            if row.financial_account_id else None,
        } for row in accounts])


class GeneralJournal(APIView):
    permission_classes = [RolePermission]
    permission_map = {"GET": ["payments.view"], "POST": ["payments.edit"]}

    def get(self, request):
        guard_balances(request)
        try:
            page_size = min(200, max(1, int(request.query_params.get("page_size", "50"))))
        except (TypeError, ValueError):
            page_size = 50
        journals = JournalEntry.objects.filter(
            company=request.user.company,
        ).prefetch_related("lines__account").order_by("-posted_at", "-pk")[:page_size]
        return Response({
            "coverage": coverage(request.user.company),
            "journals": [journal_json(row) for row in journals],
        })

    @transaction.atomic
    def post(self, request):
        guard_balances(request)
        key = request_token(request.data)
        memo = str(request.data.get("memo") or "").strip()
        if not 8 <= len(memo) <= 500:
            raise ValidationError({"memo": "Explain this journal (8–500 characters)."})
        raw_lines = request.data.get("lines")
        if not isinstance(raw_lines, list) or not 2 <= len(raw_lines) <= 20:
            raise ValidationError({"lines": "Supply 2–20 debit/credit journal lines."})
        Company.objects.select_for_update().get(pk=request.user.company_id)
        parsed = []
        for row in raw_lines:
            if not isinstance(row, dict):
                raise ValidationError({"lines": "Invalid line."})
            code = str(row.get("code") or "").strip()
            if code not in CHART or code == "9999":
                raise ValidationError({
                    "code": "Manual journals may use recognized non-cash accounts only."
                })
            debit = positive_money(row.get("debit", "0"), "debit", allow_zero=True)
            credit = positive_money(row.get("credit", "0"), "credit", allow_zero=True)
            parsed.append({"account": account_for(request.user.company_id, code),
                           "debit": debit, "credit": credit})
        previous = JournalEntry.objects.filter(
            company_id=request.user.company_id, source_key="manual:" + key,
        ).prefetch_related("lines__account").first()
        if previous:
            previous_lines = sorted(
                [(r.account.code, r.debit, r.credit) for r in previous.lines.all()]
            )
            submitted = sorted([
                (r["account"].code, r["debit"], r["credit"]) for r in parsed
            ])
            if previous.memo != memo or previous_lines != submitted:
                raise ValidationError({"requestKey": "Request key belongs to another journal."})
            return Response(journal_json(previous), status=200)
        result, _ = post_journal(
            company_id=request.user.company_id, source_key="manual:" + key,
            memo=memo, lines=parsed, created_by_id=request.user.pk,
            entry_kind="Manual", reference=str(request.data.get("reference") or "")[:160],
        )
        return Response(journal_json(
            JournalEntry.objects.prefetch_related("lines__account").get(pk=result.pk)
        ), status=201)


class AccountingStatements(APIView):
    permission_classes = [RolePermission]
    permission_map = {"GET": ["payments.view"]}

    def get(self, request, report):
        guard_balances(request)
        if report not in {"trial-balance", "profit-loss", "balance-sheet"}:
            raise ValidationError({"report": "Unsupported accounting statement."})
        start = iso_date(request.query_params.get("from"), "from", optional=True)
        end = iso_date(request.query_params.get("to"), "to", optional=True)
        if start and end and end < start:
            raise ValidationError({"to": "End date must not precede start date."})
        company = request.user.company
        # Balance sheet and trial balance are cumulative as-of statements;
        # Profit and Loss supports a period start.
        rows = report_rows(company, start if report == "profit-loss" else None, end)
        info = coverage(company)
        if report == "trial-balance":
            result = [{
                "code": row["code"], "name": row["name"], "type": row["type"],
                "debitBalance": str(max(ZERO, row["debit"] - row["credit"])),
                "creditBalance": str(max(ZERO, row["credit"] - row["debit"])),
            } for row in rows]
            debit = sum((Decimal(r["debitBalance"]) for r in result), ZERO)
            credit = sum((Decimal(r["creditBalance"]) for r in result), ZERO)
            return Response({
                "accounts": result, "totalDebit": str(debit),
                "totalCredit": str(credit), "isBalanced": debit == credit,
                "asOf": (end or timezone.localdate()).isoformat(), "coverage": info,
            })
        income = sum((
            row["credit"] - row["debit"] for row in rows if row["type"] == "Income"
        ), ZERO)
        expenses = sum((
            row["debit"] - row["credit"] for row in rows if row["type"] == "Expense"
        ), ZERO)
        profit = income - expenses
        if report == "profit-loss":
            return Response({
                "income": str(income), "expenses": str(expenses),
                "netProfit": str(profit),
                "incomeAccounts": [{
                    "code": r["code"], "name": r["name"],
                    "amount": str(r["credit"] - r["debit"]),
                } for r in rows if r["type"] == "Income"],
                "expenseAccounts": [{
                    "code": r["code"], "name": r["name"],
                    "amount": str(r["debit"] - r["credit"]),
                } for r in rows if r["type"] == "Expense"],
                "from": start.isoformat() if start else None,
                "to": (end or timezone.localdate()).isoformat(), "coverage": info,
            })
        assets = sum((
            row["debit"] - row["credit"] for row in rows if row["type"] == "Asset"
        ), ZERO)
        liabilities = sum((
            row["credit"] - row["debit"] for row in rows if row["type"] == "Liability"
        ), ZERO)
        capital = sum((
            row["credit"] - row["debit"] for row in rows if row["type"] == "Equity"
        ), ZERO)
        right = liabilities + capital + profit
        return Response({
            "assets": str(assets), "liabilities": str(liabilities),
            "equity": str(capital), "currentEarnings": str(profit),
            "liabilitiesAndEquity": str(right),
            "difference": str(assets - right), "isBalanced": assets == right,
            "asOf": (end or timezone.localdate()).isoformat(),
            "accounts": [{
                "code": r["code"], "name": r["name"], "type": r["type"],
                "balance": str(
                    r["debit"] - r["credit"] if r["type"] == "Asset"
                    else r["credit"] - r["debit"]
                ),
            } for r in rows if r["type"] in {"Asset", "Liability", "Equity"}],
            "coverage": info,
        })


class BankReconciliations(APIView):
    permission_classes = [RolePermission]
    permission_map = {"GET": ["payments.view"], "POST": ["payments.edit"]}

    def get(self, request):
        guard_balances(request)
        qs = BankReconciliation.objects.filter(
            company=request.user.company,
        ).select_related("account").order_by("-statement_date")[:100]
        return Response([{
            "id": str(r.pk), "accountId": str(r.account_id),
            "accountName": r.account.name,
            "statementDate": r.statement_date.isoformat(),
            "statementBalance": str(r.statement_balance),
            "clearedBalance": str(r.cleared_balance),
            "bookBalance": str(r.book_balance),
            "difference": str(r.difference),
            "reference": r.reference,
        } for r in qs])

    @transaction.atomic
    def post(self, request):
        guard_balances(request)
        key = request_token(request.data)
        Company.objects.select_for_update().get(pk=request.user.company_id)
        raw_ids = request.data.get("entryIds")
        if not isinstance(raw_ids, list) or not 1 <= len(raw_ids) <= 500:
            raise ValidationError({"entryIds": "Choose 1–500 cleared account ledger entries."})
        raw_keys = [str(i) for i in raw_ids]
        if len(set(raw_keys)) != len(raw_keys):
            raise ValidationError({"entryIds": "Do not select one bank entry twice."})
        account = get_object_or_404(
            WorkshopFinancialAccount.objects.select_for_update(),
            pk=request.data.get("accountId"), company_id=request.user.company_id,
            kind__in=["Bank", "UPI"],
        )
        statement_date = iso_date(request.data.get("statementDate"), "statementDate")
        statement_balance = positive_money(
            request.data.get("statementBalance"), "statementBalance", allow_zero=True,
        )
        existing = BankReconciliation.objects.filter(
            company_id=request.user.company_id, request_key=key,
        ).first()
        if existing:
            earlier = set(str(x) for x in existing.items.values_list("entry_id", flat=True))
            if (existing.account_id != account.pk or existing.statement_date != statement_date
                    or existing.statement_balance != statement_balance
                    or earlier != set(raw_keys)):
                raise ValidationError({"requestKey": "This key was used for another bank statement."})
            return Response({"id": str(existing.pk), "status": "Reconciled"}, status=200)
        entries = list(WorkshopAccountEntry.objects.select_for_update().filter(
            company_id=request.user.company_id, account=account, pk__in=raw_keys,
            posted_at__date__lte=statement_date,
        ))
        if len(entries) != len(raw_keys):
            raise ValidationError({"entryIds": "Some entries do not belong to this account/date."})
        if BankReconciliationLine.objects.filter(entry__in=entries).exists():
            raise ValidationError({"entryIds": "Some entries are already reconciled."})
        historical = BankReconciliationLine.objects.filter(
            reconciliation__company_id=request.user.company_id,
            entry__account=account,
            entry__posted_at__date__lte=statement_date,
        ).aggregate(amount=Sum("entry__amount"))["amount"] or ZERO
        cleared = account.opening_balance + historical + sum(
            (entry.amount for entry in entries), ZERO
        )
        book = account.opening_balance + (
            WorkshopAccountEntry.objects.filter(
                company_id=request.user.company_id, account=account,
                posted_at__date__lte=statement_date,
            ).aggregate(total=Sum("amount"))["total"] or ZERO
        )
        if cleared != statement_balance:
            raise ValidationError({
                "statementBalance": (
                    "Cleared bank entries plus opening balance do not match this "
                    f"statement. Cleared total: {cleared}."
                )
            })
        reconciliation = BankReconciliation.objects.create(
            company_id=request.user.company_id, account=account,
            statement_date=statement_date,
            statement_balance=statement_balance,
            cleared_balance=cleared, book_balance=book,
            difference=book - cleared,
            request_key=key, reference=str(request.data.get("reference") or "")[:160],
            created_by=request.user,
        )
        BankReconciliationLine.objects.bulk_create([
            BankReconciliationLine(reconciliation=reconciliation, entry=entry)
            for entry in entries
        ])
        return Response({
            "id": str(reconciliation.pk), "status": "Reconciled",
            "statementBalance": str(statement_balance),
            "bookBalance": str(book), "unclearedDifference": str(book - cleared),
            "matchedEntries": len(entries),
        }, status=201)


class BankReconciliationCandidates(APIView):
    permission_classes = [RolePermission]
    permission_map = {"GET": ["payments.view"]}

    def get(self, request):
        guard_balances(request)
        account = get_object_or_404(
            WorkshopFinancialAccount,
            company=request.user.company, pk=request.query_params.get("accountId"),
            kind__in=["Bank", "UPI"],
        )
        statement_date = iso_date(
            request.query_params.get("statementDate") or timezone.localdate(), "statementDate",
        )
        candidates = WorkshopAccountEntry.objects.filter(
            company=request.user.company, account=account,
            posted_at__date__lte=statement_date,
            bank_reconciliation_line__isnull=True,
        ).order_by("posted_at", "pk")[:500]
        return Response([{
            "id": str(row.pk), "date": row.posted_at.date().isoformat(),
            "type": row.entry_type, "amount": str(row.amount),
            "reference": row.reference,
        } for row in candidates])
