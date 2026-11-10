from decimal import Decimal

from django.db.models import Sum
from rest_framework import serializers

from .models import Expense


class ExpenseSerializer(serializers.ModelSerializer):
    paidAmount = serializers.SerializerMethodField()
    balanceDue = serializers.SerializerMethodField()
    settlementHistory = serializers.SerializerMethodField()

    class Meta:
        model = Expense
        exclude = ("company", "branch")
        read_only_fields = ("created_by",)

    def validate_amount(self, amount):
        if not amount.is_finite() or amount <= 0:
            raise serializers.ValidationError("Expense amount must be positive.")
        return amount

    def validate_tax(self, amount):
        if not amount.is_finite() or amount < 0:
            raise serializers.ValidationError("Tax cannot be negative.")
        return amount

    def validate(self, attrs):
        attrs = super().validate(attrs)
        budget = attrs.get(
            "budget_item", self.instance.budget_item if self.instance else None,
        )
        if budget is None:
            return attrs
        user = self.context["request"].user
        if budget.company_id != user.company_id:
            raise serializers.ValidationError({
                "budget_item": "Select a budget from your own workshop.",
            })
        if not budget.is_active and (not self.instance or self.instance.budget_item_id != budget.id):
            raise serializers.ValidationError({"budget_item": "This budget item is inactive."})
        expense_date = attrs.get("date", self.instance.date if self.instance else None)
        category = attrs.get("category", self.instance.category if self.instance else "")
        if (not expense_date or expense_date.year != budget.month.year or
                expense_date.month != budget.month.month):
            raise serializers.ValidationError({
                "budget_item": "The budget month must match the expense date.",
            })
        if category.casefold().strip() != budget.category.casefold().strip():
            raise serializers.ValidationError({
                "budget_item": "Budget category must match the expense category.",
            })
        return attrs

    def _paid(self, obj):
        if hasattr(obj, "_finance_paid"):
            return obj._finance_paid
        return obj.settlements.filter(
            company_id=obj.company_id, reversed_at__isnull=True
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")

    def get_settlementHistory(self, obj):
        rows = obj.settlements.select_related("account", "recorded_by").order_by("-posted_at", "-created_at")[:100]
        return [{
            "id": str(row.pk),
            "accountId": str(row.account_id),
            "accountName": row.account.name,
            "amount": str(row.amount),
            "method": row.method,
            "reference": row.reference,
            "postedAt": row.posted_at.isoformat(),
            "recordedBy": row.recorded_by.name if row.recorded_by else "",
            "reversedAt": row.reversed_at.isoformat() if row.reversed_at else None,
            "reversalReason": row.reversal_reason,
        } for row in rows]

    def get_paidAmount(self, obj):
        return str(self._paid(obj).quantize(Decimal("0.01")))

    def get_balanceDue(self, obj):
        due = max(Decimal("0.00"), obj.amount - self._paid(obj))
        return str(due.quantize(Decimal("0.01")))
