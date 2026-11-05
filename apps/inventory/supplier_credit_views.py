"""Supplier credit against a real received PurchaseOrder; Expense is the cash outflow.

No credit is inferred from an unreceived PO or free-form Stock In movement.
No customer Payment is created; Expenses remains the canonical payment ledger.
"""
from datetime import date
from decimal import Decimal, InvalidOperation
from uuid import UUID

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.accounts.permissions import RolePermission
from apps.expenses.models import Expense
from .models import PurchaseOrder, SupplierCredit, SupplierCreditPayment


def paid_for(credit):
    """Use only recorded, linked expense amounts; never trust client balances."""
    return credit.payments.aggregate(total=Sum("expense__amount"))["total"] or Decimal("0.00")


def outstanding_for(credit):
    return max(Decimal("0.00"), credit.purchase_order.total - paid_for(credit))


def payment_amount(value):
    try:
        amount = Decimal(str(value))
    except (ValueError, InvalidOperation, TypeError):
        raise ValidationError({"amount": "Enter a valid amount."})
    if not amount.is_finite() or amount <= 0 or amount.as_tuple().exponent < -2 or amount >= Decimal("10000000000"):
        raise ValidationError({"amount": "Enter a positive amount with at most two decimals."})
    return amount


class SupplierCreditSerializer(serializers.ModelSerializer):
    purchaseOrder = serializers.UUIDField(source="purchase_order_id", read_only=True)
    purchaseNumber = serializers.CharField(source="purchase_order.number", read_only=True)
    supplierId = serializers.UUIDField(source="purchase_order.supplier_id", read_only=True)
    supplierName = serializers.CharField(source="purchase_order.supplier.name", read_only=True)
    total = serializers.DecimalField(source="purchase_order.total", max_digits=12, decimal_places=2, read_only=True)
    dueDate = serializers.DateField(source="due_date", read_only=True)
    paid = serializers.SerializerMethodField()
    outstanding = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()

    class Meta:
        model = SupplierCredit
        fields = ("id", "purchaseOrder", "purchaseNumber", "supplierId",
                  "supplierName", "total", "dueDate", "paid", "outstanding",
                  "status", "created_at")

    def get_paid(self, obj):
        return str(paid_for(obj))

    def get_outstanding(self, obj):
        return str(outstanding_for(obj))

    def get_status(self, obj):
        paid = paid_for(obj)
        if paid >= obj.purchase_order.total:
            return "Settled"
        if paid > 0:
            return "Partially Paid"
        if obj.due_date and obj.due_date < timezone.localdate():
            return "Overdue"
        return "Outstanding"


class SupplierCreditPaymentSerializer(serializers.ModelSerializer):
    creditId = serializers.UUIDField(source="credit_id", read_only=True)
    supplierId = serializers.UUIDField(source="credit.purchase_order.supplier_id", read_only=True)
    supplierName = serializers.CharField(source="credit.purchase_order.supplier.name", read_only=True)
    purchaseNumber = serializers.CharField(source="credit.purchase_order.number", read_only=True)
    expenseId = serializers.UUIDField(source="expense_id", read_only=True)
    amount = serializers.DecimalField(source="expense.amount", max_digits=12, decimal_places=2, read_only=True)
    method = serializers.CharField(source="expense.payment_method", read_only=True)
    reference = serializers.CharField(source="expense.reference", read_only=True)
    date = serializers.DateField(source="expense.date", read_only=True)

    class Meta:
        model = SupplierCreditPayment
        fields = ("id", "creditId", "supplierId", "supplierName",
                  "purchaseNumber", "expenseId", "amount", "method",
                  "reference", "date", "created_at")


class TenantSupplierQueryset:
    def get_queryset(self):
        user = self.request.user
        if not getattr(user, "company_id", None):
            return self.queryset.none()
        qs = self.queryset.filter(company_id=user.company_id)
        supplier_id = self.request.query_params.get("supplier")
        if supplier_id:
            try:
                qs = qs.filter(
                    **({"purchase_order__supplier_id": UUID(str(supplier_id))}
                       if self._supplier_credit else
                       {"credit__purchase_order__supplier_id": UUID(str(supplier_id))})
                )
            except (ValueError, TypeError, AttributeError):
                return qs.none()
        return qs


class SupplierCreditViewSet(TenantSupplierQueryset, mixins.ListModelMixin,
                            mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [RolePermission]
    action_permission_map = {
        "list": ["stock.view", "expenses.view"],
        "retrieve": ["stock.view", "expenses.view"],
        "create": ["stock.view", "stock.edit", "expenses.create"],
    }
    _supplier_credit = True
    queryset = SupplierCredit.objects.select_related("purchase_order__supplier")
    serializer_class = SupplierCreditSerializer
    http_method_names = ["get", "post", "head", "options"]

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        try:
            po_id = UUID(str(request.data.get("purchaseOrder")))
        except (TypeError, ValueError, AttributeError):
            raise ValidationError({"purchaseOrder": "Select an existing received Purchase Order."})
        po = PurchaseOrder.objects.select_for_update().select_related("supplier").filter(
            id=po_id, company_id=request.user.company_id,
        ).first()
        if not po or po.supplier.company_id != request.user.company_id:
            raise ValidationError({"purchaseOrder": "Purchase Order not found in this workshop."})
        if po.status not in {"Received", "Completed"} or po.total <= Decimal("0"):
            raise ValidationError({"purchaseOrder": "Only fully received Purchase Orders with a positive total can be credited."})
        if SupplierCredit.objects.filter(purchase_order=po).exists():
            raise ValidationError({"purchaseOrder": "This purchase already has a credit record."})

        due_raw = request.data.get("dueDate") or None
        try:
            due = date.fromisoformat(str(due_raw)) if due_raw else None
        except (TypeError, ValueError):
            raise ValidationError({"dueDate": "Use YYYY-MM-DD for the due date."})

        credit = SupplierCredit.objects.create(
            company=request.user.company, branch=po.branch,
            purchase_order=po, due_date=due,
        )
        return Response(self.get_serializer(credit).data, status=status.HTTP_201_CREATED)


class SupplierCreditPaymentViewSet(TenantSupplierQueryset, mixins.ListModelMixin,
                                   mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [RolePermission]
    action_permission_map = {
        "list": ["stock.view", "expenses.view"],
        "retrieve": ["stock.view", "expenses.view"],
        "create": ["stock.view", "expenses.create"],
    }
    _supplier_credit = False
    queryset = SupplierCreditPayment.objects.select_related(
        "expense", "credit__purchase_order__supplier"
    )
    serializer_class = SupplierCreditPaymentSerializer
    http_method_names = ["get", "post", "head", "options"]

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        company = request.user.company
        try:
            key = UUID(str(request.data.get("idempotencyKey")))
            credit_id = UUID(str(request.data.get("credit")))
        except (TypeError, ValueError, AttributeError):
            raise ValidationError({"message": "Valid credit and idempotencyKey UUIDs are required."})

        amount = payment_amount(request.data.get("amount"))
        method = str(request.data.get("method") or "").strip()
        reference = str(request.data.get("reference") or "").strip()
        if method not in {"Cash", "Bank", "UPI"}:
            raise ValidationError({"method": "Choose Cash, Bank, or UPI."})
        if method != "Cash" and not reference:
            raise ValidationError({"reference": "A bank/UPI reference is required."})
        if len(reference) > 120:
            raise ValidationError({"reference": "Reference is too long."})

        # Serializes concurrent payments on the same PO on transactional DBs.
        credit = SupplierCredit.objects.select_related("purchase_order__supplier").select_for_update().filter(
            id=credit_id, company_id=company.id,
        ).first()
        if not credit:
            raise ValidationError({"credit": "Supplier credit not found in this workshop."})
        existing = SupplierCreditPayment.objects.select_related("expense").filter(
            company_id=company.id, idempotency_key=key
        ).first()
        if existing:
            if (existing.credit_id != credit.id or existing.expense.amount != amount
                    or existing.expense.payment_method != method
                    or existing.expense.reference != reference):
                raise ValidationError({"idempotencyKey": "This request ID was already used for a different payment."})
            return Response(self.get_serializer(existing).data, status=status.HTTP_200_OK)

        if amount > outstanding_for(credit):
            raise ValidationError({"amount": "Payment exceeds the outstanding supplier credit."})

        expense = Expense.objects.create(
            company=company, branch=credit.branch,
            date=timezone.localdate(), category="Supplier Credit Settlement",
            vendor=credit.purchase_order.supplier.name,
            description=f"Supplier credit settlement for PO {credit.purchase_order.number}",
            amount=amount, tax=Decimal("0"), payment_method=method,
            reference=reference, status="Paid", created_by=request.user,
        )
        allocation = SupplierCreditPayment.objects.create(
            company=company, branch=credit.branch, credit=credit, expense=expense,
            idempotency_key=key, recorded_by=request.user,
        )
        return Response(self.get_serializer(allocation).data, status=status.HTTP_201_CREATED)
