from decimal import Decimal
from django.db import models
from common.models import CompanyOwnedModel
class ExpenseBudgetItem(CompanyOwnedModel):
    """A monthly operating-expense budget, not a ledger posting."""
    name = models.CharField(max_length=120)
    category = models.CharField(max_length=100)
    month = models.DateField(help_text="First day of the budget month.")
    planned_amount = models.DecimalField(max_digits=12, decimal_places=2)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-month", "category", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "month", "name"],
                name="unique_expense_budget_name_month",
            ),
            models.CheckConstraint(
                condition=models.Q(planned_amount__gte=0),
                name="expense_budget_nonnegative",
            ),
        ]


class Expense(CompanyOwnedModel):
    date=models.DateField()
    category=models.CharField(max_length=100)
    vendor=models.CharField(max_length=180,blank=True)
    description=models.TextField(blank=True)
    amount=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    tax=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    payment_method=models.CharField(max_length=50,blank=True)
    reference=models.CharField(max_length=120,blank=True)
    attachment=models.URLField(blank=True)
    status=models.CharField(max_length=30,default="Unpaid")
    created_by=models.ForeignKey("accounts.User",on_delete=models.SET_NULL,null=True,blank=True)
    # Finance account linkage
    account=models.ForeignKey("inventory.WorkshopFinancialAccount",on_delete=models.SET_NULL,null=True,blank=True,related_name="expense_payments")
    budget_item=models.ForeignKey(
        ExpenseBudgetItem, on_delete=models.PROTECT, null=True, blank=True,
        related_name="expenses",
    )
    class Meta: ordering=["-date","-created_at"]


class ExpenseSettlement(CompanyOwnedModel):
    """Audited, idempotent settlement of an expense payable."""
    expense = models.ForeignKey(Expense, on_delete=models.PROTECT, related_name="settlements")
    account = models.ForeignKey(
        "inventory.WorkshopFinancialAccount", on_delete=models.PROTECT,
        related_name="expense_settlements",
    )
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    method = models.CharField(max_length=50, default="Cash")
    reference = models.CharField(max_length=120, blank=True)
    idempotency_key = models.CharField(max_length=128)
    posted_at = models.DateTimeField()
    recorded_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True, blank=True)
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversed_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="reversed_expense_settlements",
    )
    reversal_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-posted_at", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "idempotency_key"],
                name="unique_expense_settlement_request",
            ),
        ]
