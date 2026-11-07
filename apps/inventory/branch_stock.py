"""Atomic branch-stock helpers. StockItem.on_hand remains the company-wide total.

Legacy items without a branch balance are initialized only while the parent
StockItem row is locked by the calling transaction. Never infer a second branch
balance from global on_hand after an initial allocation.
"""
from decimal import Decimal

from rest_framework.exceptions import ValidationError

from .models import StockBranchBalance


ZERO = Decimal("0.00")


def locked_branch_balance(item, branch):
    """Caller MUST hold select_for_update() on item inside transaction.atomic."""
    if branch is None:
        raise ValidationError({"branch": "Choose a workshop branch before posting inventory."})
    if str(branch.company_id) != str(item.company_id):
        raise ValidationError({"branch": "Branch belongs to a different workshop."})
    rows = StockBranchBalance.objects.select_for_update().filter(
        company_id=item.company_id, item=item,
    )
    row = rows.filter(branch=branch).first()
    if row:
        return row
    if not rows.exists():
        # Historical stock was held in a single branch or had no assigned
        # branch. Allocate its opening balance exactly once, never twice.
        seed_branch = item.branch_id or branch.pk
        if str(seed_branch) != str(branch.pk):
            raise ValidationError({
                "branch": "Existing stock belongs to the original branch. Transfer it first.",
            })
        return StockBranchBalance.objects.create(
            company_id=item.company_id, item=item, branch=branch,
            quantity=item.on_hand, reserved=item.reserved,
        )
    return StockBranchBalance.objects.create(
        company_id=item.company_id, item=item, branch=branch,
        quantity=ZERO, reserved=ZERO,
    )


def available_in_branch(row):
    return row.quantity - row.reserved


def move_balance(row, delta):
    if not delta.is_finite() or delta.as_tuple().exponent < -2:
        raise ValidationError({"quantity": "Invalid stock precision."})
    if delta < ZERO and available_in_branch(row) < -delta:
        raise ValidationError({"quantity": "Insufficient stock in the selected branch."})
    if row.quantity + delta < ZERO:
        raise ValidationError({"quantity": "Stock cannot become negative."})
    row.quantity += delta
    row.save(update_fields=["quantity", "updated_at"])
    return row


def branch_stock_snapshot(item, branch):
    if not branch:
        return None
    row = StockBranchBalance.objects.filter(
        company_id=item.company_id, item=item, branch=branch,
    ).first()
    if row:
        return {"branchOnHand": str(row.quantity), "branchAvailable": str(available_in_branch(row))}
    if StockBranchBalance.objects.filter(item=item, company_id=item.company_id).exists():
        return {"branchOnHand": "0.00", "branchAvailable": "0.00"}
    if not item.branch_id or str(item.branch_id) == str(branch.pk):
        return {
            "branchOnHand": str(item.on_hand),
            "branchAvailable": str(item.on_hand - item.reserved),
        }
    return {"branchOnHand": "0.00", "branchAvailable": "0.00"}
