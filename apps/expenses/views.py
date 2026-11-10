"""Workshop expenses are liabilities; only a settlement moves account money."""
from decimal import Decimal, InvalidOperation
from datetime import date
import re
from uuid import uuid4

from django.db import transaction
from django.db.models import Sum, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from common.viewsets import CompanyScopedModelViewSet
from apps.inventory.models import WorkshopFinancialAccount, WorkshopAccountEntry
from apps.finance.views import account_balance
from .models import Expense, ExpenseSettlement, ExpenseBudgetItem
from .serializers import ExpenseSerializer


def positive_money(value, label="amount"):
    try:
        amount = Decimal(str(value))
        if (not amount.is_finite() or amount <= 0 or
                amount.as_tuple().exponent < -2 or amount >= Decimal("1000000000000")):
            raise ValueError()
        return amount.quantize(Decimal("0.01"))
    except (ValueError, InvalidOperation, TypeError):
        raise ValidationError({label: "Enter a positive amount with at most two decimals."})


def settled_amount(expense):
    return ExpenseSettlement.objects.filter(
        expense=expense, company_id=expense.company_id, reversed_at__isnull=True
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")


def update_settlement_status(expense, amount):
    expense.status = (
        "Paid" if amount >= expense.amount else
        "Partially Paid" if amount > 0 else "Unpaid"
    )
    expense.save(update_fields=["status", "updated_at"])


class ExpenseViewSet(CompanyScopedModelViewSet):
    queryset = Expense.objects.select_related("account", "created_by").all()
    serializer_class = ExpenseSerializer
    action_permission_map = {
        "settle": "expenses.edit",
        "reverse_settlement": "expenses.edit",
        "budgets": {"GET": "expenses.view", "POST": "expenses.edit"},
        "budget_detail": "expenses.edit",
    }

    def _validate_purchase_not_duplicate(self, category):
        normalized = str(category or "").strip().casefold()
        if normalized in {
            "supplier payments", "supplier payment", "stock purchase",
            "spare parts", "spare part purchase", "inventory purchase",
            "supplier bill", "supplier credit",
        }:
            raise ValidationError({
                "category": "Supplier purchases and their payments must be linked to a posted Supplier Bill. "
                            "Use Inventory → Supplier Purchase / Supplier Payments, not a separate Expense debit."
            })
        if normalized in {"staff salary", "daily wages", "overtime", "salary", "payroll"}:
            raise ValidationError({
                "category": "Staff wages and salary must use Payroll Payments, not a duplicate Expense."
            })

    @staticmethod
    def _budget_month(raw):
        value = str(raw or "").strip()
        if not re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", value):
            raise ValidationError({"month": "Use a valid budget month (YYYY-MM)."})
        return date.fromisoformat(value + "-01")

    @staticmethod
    def _budget_rows(request, month):
        budgets = list(ExpenseBudgetItem.objects.filter(
            company=request.user.company, month=month,
        ).order_by("category", "name"))
        # Budget consumption is recognized once on the expense date, not
        # again when an expense's bank settlement is posted.
        actuals = Expense.objects.filter(
            company=request.user.company, budget_item__in=budgets,
        ).exclude(
            Q(status__iexact="Draft") | Q(status__iexact="Rejected") |
            Q(status__iexact="Cancelled") | Q(status__iexact="Pending Approval")
        ).values("budget_item_id").annotate(spent=Sum("amount"))
        spent_by_id = {entry["budget_item_id"]: entry["spent"] for entry in actuals}
        def money(value):
            return format(Decimal(value).quantize(Decimal("0.01")), ".2f")

        return [{
            "id": str(budget.pk),
            "name": budget.name,
            "category": budget.category,
            "month": budget.month.strftime("%Y-%m"),
            "plannedAmount": money(budget.planned_amount),
            "spentAmount": money(spent_by_id.get(budget.pk, Decimal("0.00"))),
            "remainingAmount": money(
                budget.planned_amount - spent_by_id.get(budget.pk, Decimal("0.00"))
            ),
            "isActive": budget.is_active,
        } for budget in budgets]

    @action(detail=False, methods=["get", "post"], url_path="budgets")
    @transaction.atomic
    def budgets(self, request):
        if request.method == "GET":
            month = self._budget_month(
                request.query_params.get("month") or timezone.localdate().strftime("%Y-%m")
            )
            return Response(self._budget_rows(request, month))
        raw = request.data
        month = self._budget_month(raw.get("month"))
        name = str(raw.get("name") or "").strip()
        category = str(raw.get("category") or "").strip()
        if not (2 <= len(name) <= 120):
            raise ValidationError({"name": "Budget item name must be 2-120 characters."})
        if not (2 <= len(category) <= 100):
            raise ValidationError({"category": "Choose a valid operating-expense category."})
        self._validate_purchase_not_duplicate(category)
        amount = positive_money(raw.get("plannedAmount"), "plannedAmount")
        budget, created = ExpenseBudgetItem.objects.get_or_create(
            company=request.user.company, month=month, name=name,
            defaults={
                "branch": request.user.branch, "category": category,
                "planned_amount": amount,
            },
        )
        if not created:
            raise ValidationError({"name": "A budget item with this name already exists for that month."})
        return Response({
            "id": str(budget.pk), "name": budget.name, "category": budget.category,
            "month": month.strftime("%Y-%m"), "plannedAmount": str(amount),
            "spentAmount": "0.00", "remainingAmount": str(amount),
            "isActive": True,
        }, status=201)

    @action(detail=False, methods=["patch"], url_path=r"budgets/(?P<budget_id>[^/.]+)")
    @transaction.atomic
    def budget_detail(self, request, budget_id=None):
        budget = get_object_or_404(
            ExpenseBudgetItem.objects.select_for_update(),
            pk=budget_id, company=request.user.company,
        )
        changed = []
        if "plannedAmount" in request.data:
            budget.planned_amount = positive_money(
                request.data["plannedAmount"], "plannedAmount",
            )
            changed.append("planned_amount")
        if "isActive" in request.data:
            if not isinstance(request.data["isActive"], bool):
                raise ValidationError({"isActive": "Choose true or false."})
            budget.is_active = request.data["isActive"]
            changed.append("is_active")
        if not changed:
            raise ValidationError({"budget": "Provide plannedAmount or isActive."})
        budget.save(update_fields=changed + ["updated_at"])
        rows = self._budget_rows(request, budget.month)
        return Response(next(row for row in rows if row["id"] == str(budget.pk)))

    def _post_settlement(self, request, expense, data):
        """Call inside transaction.atomic with expense locked."""
        company = request.user.company
        self._validate_purchase_not_duplicate(expense.category)
        amount = positive_money(data.get("amount"))
        key = str(data.get("idempotencyKey") or uuid4().hex).strip()
        if not key or len(key) > 128:
            raise ValidationError({"idempotencyKey": "Invalid settlement request key."})

        existing = ExpenseSettlement.objects.select_for_update().filter(
            company=company, idempotency_key=key
        ).first()
        if existing:
            if existing.expense_id != expense.id or existing.amount != amount:
                raise ValidationError({"idempotencyKey": "This key belongs to a different settlement."})
            return existing, False

        if expense.status.strip().lower() in {"draft", "rejected", "cancelled", "pending approval"}:
            raise ValidationError({"status": "Approve the expense before paying it."})
        paid_before = settled_amount(expense)
        # Historical 'Paid' expenses have no account posting. They must be
        # reconciled by an accountant, never silently charged a second time.
        if expense.status.strip().lower() == "paid" and paid_before == 0:
            raise ValidationError({"status": "Legacy paid expense needs reconciliation before another debit."})
        if paid_before + amount > expense.amount:
            raise ValidationError({"amount": "Payment exceeds the expense balance due."})

        account_id = data.get("account") or data.get("accountId")
        if not account_id:
            raise ValidationError({"account": "Choose a bank or cash account."})
        account = get_object_or_404(
            WorkshopFinancialAccount.objects.select_for_update(),
            pk=account_id, company=company, is_active=True
        )
        if account_balance(account) < amount:
            raise ValidationError({"amount": "Insufficient funds in the selected account."})
        method = str(data.get("method") or expense.payment_method or "Cash").strip()
        from apps.finance.channels import validate_payment_channel
        validate_payment_channel(method, account)
        if not method or len(method) > 50:
            raise ValidationError({"method": "Invalid payment method."})
        reference = str(data.get("reference") or expense.reference or "").strip()[:120]
        now = timezone.now()
        settlement = ExpenseSettlement.objects.create(
            company=company, branch=request.user.branch,
            expense=expense, account=account, amount=amount,
            method=method, reference=reference, idempotency_key=key,
            posted_at=now, recorded_by=request.user,
        )
        WorkshopAccountEntry.objects.create(
            company=company, branch=request.user.branch, account=account,
            amount=-amount, entry_type="expense_payment",
            source_key=f"expense_settlement_{settlement.pk}",
            reference=reference or f"Expense {expense.pk}",
            posted_at=now, created_by=request.user,
        )
        expense.account = account
        expense.payment_method = method
        expense.save(update_fields=["account", "payment_method", "updated_at"])
        update_settlement_status(expense, paid_before + amount)
        return settlement, True

    @transaction.atomic
    def perform_create(self, serializer):
        # A successful POST might be retried after the client loses its
        # response. Do not create a new Expense before detecting an existing
        # posted settlement with this request key.
        key = str(self.request.data.get("idempotencyKey") or "").strip()
        if key and ExpenseSettlement.objects.filter(
            company=self.request.user.company, idempotency_key=key,
        ).exists():
            raise ValidationError({
                "idempotencyKey": "This expense payment request was already posted. "
                                  "Refresh expense history instead of submitting it again."
            })
        self._validate_purchase_not_duplicate(serializer.validated_data.get("category"))
        desired_status = str(serializer.validated_data.get("status", "Unpaid")).strip().lower()
        account = serializer.validated_data.get("account")
        if desired_status == "paid" and not account:
            raise ValidationError({"account": "Select an account to record a paid expense."})
        expense = serializer.save(
            company=self.request.user.company, branch=self.request.user.branch,
            created_by=self.request.user,
            status="Unpaid" if desired_status == "paid" else serializer.validated_data.get("status", "Unpaid"),
        )
        if desired_status == "paid":
            self._post_settlement(self.request, expense, {
                "account": str(account.pk),
                "amount": str(expense.amount),
                "method": expense.payment_method,
                "reference": expense.reference,
                "idempotencyKey": self.request.data.get("idempotencyKey") or uuid4().hex,
            })

    @transaction.atomic
    def perform_update(self, serializer):
        expense = Expense.objects.select_for_update().get(
            pk=serializer.instance.pk, company=self.request.user.company
        )
        if hasattr(expense, "supplier_credit_allocation"):
            raise ValidationError({"message": "Supplier settlements are immutable. Reverse through supplier finance."})
        if expense.settlements.exists():
            immutable = {"amount", "tax", "date", "account", "payment_method", "status", "category", "budget_item"}
            changed = immutable.intersection(serializer.validated_data)
            if changed:
                raise ValidationError({"message": "Posted expense amounts/status cannot be edited. Use settlement reversal."})
        elif str(serializer.validated_data.get("status", "")).strip().lower() == "paid":
            if str(expense.status).strip().lower() != "paid":
                raise ValidationError({"status": "Use the settlement action to debit a bank or cash account."})
        serializer.save()

    def perform_destroy(self, instance):
        if hasattr(instance, "supplier_credit_allocation") or instance.settlements.exists():
            raise ValidationError({"message": "Posted supplier/expense payments cannot be deleted. Reverse them instead."})
        if str(instance.status).strip().lower() == "paid":
            raise ValidationError({"status": "Reconcile this legacy paid expense instead of deleting it."})
        instance.delete()

    @action(detail=True, methods=["post"], url_path="settle")
    @transaction.atomic
    def settle(self, request, pk=None):
        expense = get_object_or_404(
            Expense.objects.select_for_update(), company=request.user.company, pk=pk
        )
        settlement, created = self._post_settlement(request, expense, request.data)
        return Response({
            "settlementId": str(settlement.pk),
            "paidAmount": str(settled_amount(expense)),
            "balanceDue": str(max(Decimal("0"), expense.amount - settled_amount(expense))),
            "accountBalance": str(account_balance(settlement.account)),
            "status": expense.status,
            "reversed": settlement.reversed_at is not None,
        }, status=201 if created else 200)

    @action(
        detail=True, methods=["post"],
        url_path=r"settlements/(?P<settlement_id>[^/.]+)/reverse",
    )
    @transaction.atomic
    def reverse_settlement(self, request, pk=None, settlement_id=None):
        expense = get_object_or_404(
            Expense.objects.select_for_update(), company=request.user.company, pk=pk
        )
        settlement = get_object_or_404(
            ExpenseSettlement.objects.select_for_update(),
            company=request.user.company, expense=expense, pk=settlement_id,
        )
        if settlement.reversed_at:
            return Response({"settlementId": str(settlement.pk), "status": expense.status, "alreadyReversed": True})
        reason = str(request.data.get("reason") or "").strip()
        if not reason or len(reason) > 1500:
            raise ValidationError({"reason": "Enter an audit reason (up to 1500 characters)."})
        WorkshopAccountEntry.objects.create(
            company=request.user.company, branch=request.user.branch,
            account=settlement.account, amount=settlement.amount,
            entry_type="expense_reversal",
            source_key=f"expense_settlement_reverse_{settlement.pk}",
            reference=reason[:120], posted_at=timezone.now(),
            created_by=request.user,
        )
        settlement.reversed_at = timezone.now()
        settlement.reversed_by = request.user
        settlement.reversal_reason = reason
        settlement.save(update_fields=["reversed_at", "reversed_by", "reversal_reason", "updated_at"])
        update_settlement_status(expense, settled_amount(expense))
        return Response({
            "settlementId": str(settlement.pk), "status": expense.status,
            "paidAmount": str(settled_amount(expense)),
            "accountBalance": str(account_balance(settlement.account)),
        })
