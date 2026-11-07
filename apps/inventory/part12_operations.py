"""Safe full-stack stock workflows: paired branch transfers, verified returns and counts."""
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from django.db import transaction
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.branches.models import Branch
from apps.companies.models import Company
from .branch_stock import locked_branch_balance, move_balance
from .models import StockAudit, StockItem, StockMovement, StockTransfer
from .request_keys import stock_request_key, replay_movement


def quantity(value):
    try:
        amount = Decimal(str(value))
    except (ValueError, TypeError, InvalidOperation):
        raise ValidationError({"quantity": "Enter a valid quantity."})
    if not amount.is_finite() or amount <= 0 or amount.as_tuple().exponent < -2 or amount > Decimal("99999999.99"):
        raise ValidationError({"quantity": "Quantity must be positive with no more than two decimals."})
    return amount


def branch_from(company, value):
    if not value:
        return None
    qs = Branch.objects.filter(company=company, is_active=True)
    try:
        return qs.filter(pk=value).first() or qs.filter(name=value).first()
    except (TypeError, ValueError, DjangoValidationError):
        return qs.filter(name=str(value)).first()


@transaction.atomic
def transfer_inventory(request):
    """Atomic zero-net company transfer, with two immutable branch ledger legs."""
    company = request.user.company
    key = stock_request_key(request)
    if not key:
        raise ValidationError({"requestKey": "A request key is required for a branch transfer."})
    qty = quantity(request.data.get("quantity"))
    Company.objects.select_for_update().get(pk=company.pk)
    source = branch_from(company, request.data.get("fromBranch")) or request.user.branch
    target = branch_from(company, request.data.get("toBranch"))
    if not source or not target or source.pk == target.pk or source.company_id != company.pk:
        raise ValidationError({"toBranch": "Choose two distinct active branches in this workshop."})
    previous = StockTransfer.objects.filter(company=company, request_key=key).first()
    if previous:
        original = previous.items[0] if previous.items else {}
        if (str(original.get("itemId")) != str(request.data.get("itemId"))
                or Decimal(str(original.get("quantity", "0"))) != qty
                or previous.from_branch_id != source.pk
                or previous.to_branch_id != target.pk):
            raise ValidationError({"requestKey": "This key belongs to another stock transfer."})
        return previous, False
    if request.user.branch_id and source.pk != request.user.branch_id:
        from apps.accounts.permissions import user_has_permission
        if not user_has_permission(request.user, "stock.manage"):
            raise ValidationError({"fromBranch": "Only the source branch may transfer inventory."})
    try:
        item = StockItem.objects.select_for_update().get(
            company=company, pk=request.data.get("itemId"),
        )
    except (StockItem.DoesNotExist, ValueError, TypeError):
        raise ValidationError({"itemId": "Select a valid workshop product."})
    origin = locked_branch_balance(item, source)
    destination = locked_branch_balance(item, target)
    move_balance(origin, -qty)
    move_balance(destination, qty)
    transfer = StockTransfer.objects.create(
        company=company, branch=source, request_key=key,
        number="TRF-" + uuid4().hex[:12].upper(),
        from_branch=source, to_branch=target, status="Completed",
        items=[{"itemId": str(item.pk), "quantity": str(qty)}],
        transferred_at=timezone.now(),
    )
    for branch, kind, amount in (
        (source, "transfer_out", -qty), (target, "transfer_in", qty),
    ):
        StockMovement.objects.create(
            company=company, branch=branch, item=item,
            movement_type=kind, quantity=amount,
            unit_cost=item.cost_price, unit_selling_price=item.selling_price,
            reference="TRANSFER:" + str(transfer.pk),
            note=f"{source.name} to {target.name}", created_by=request.user,
        )
    # The same SKU now has quantities in both branches; global on_hand is not altered.
    return transfer, True


@transaction.atomic
def return_issued_stock(request):
    """Accept a usable return only against real issued units, not an arbitrary credit."""
    company = request.user.company
    if request.data.get("condition", "Reusable") != "Reusable":
        raise ValidationError({"condition": "Damaged goods must be written off through audited stock adjustment."})
    qty = quantity(request.data.get("quantity"))
    key = stock_request_key(request)
    issue_id = request.data.get("issueMovementId")
    if not issue_id:
        raise ValidationError({"issueMovementId": "Choose an original issued movement."})
    Company.objects.select_for_update().get(pk=company.pk)
    try:
        original = StockMovement.objects.select_for_update().get(
            pk=issue_id, company=company, movement_type__in=["sale", "job_issue"],
            quantity__lt=0,
        )
    except (StockMovement.DoesNotExist, TypeError, ValueError):
        raise ValidationError({"issueMovementId": "Valid previous issue was not found."})
    item = StockItem.objects.select_for_update().get(pk=original.item_id, company=company)
    if original.branch_id != request.user.branch_id:
        raise ValidationError({"branch": "Return stock to the branch where it was issued."})
    if request.data.get("itemId") and str(item.pk) != str(request.data["itemId"]):
        raise ValidationError({"itemId": "Return product does not match the issued product."})
    old = replay_movement(request, key, item=item, kind="return", quantity=qty)
    if old:
        if old.origin_movement_id != original.pk:
            raise ValidationError({"requestKey": "This key belongs to another issued movement."})
        return old, False
    previous_qty = original.return_movements.aggregate(
        value=Sum("quantity"),
    )["value"] or Decimal("0.00")
    if previous_qty + qty > -original.quantity:
        raise ValidationError({"quantity": "Returned units exceed the original issue."})
    balance = locked_branch_balance(item, request.user.branch)
    move_balance(balance, qty)
    item.on_hand += qty
    item.save(update_fields=["on_hand", "updated_at"])
    movement = StockMovement.objects.create(
        company=company, branch=request.user.branch, item=item,
        request_key=key, origin_movement=original, movement_type="return",
        quantity=qty, unit_cost=original.unit_cost,
        unit_selling_price=original.unit_selling_price,
        reference=original.reference, job=original.job,
        note=str(request.data.get("notes") or "Returned usable stock.")[:2000],
        created_by=request.user,
    )
    return movement, True


@transaction.atomic
def count_inventory(request):
    """Physical count posts exactly one signed variance with an audit trail."""
    company = request.user.company
    raw = request.data.get("countedQty", request.data.get("counted_qty"))
    try:
        qty = Decimal(str(raw))
    except (ValueError, TypeError, InvalidOperation):
        raise ValidationError({"countedQty": "Enter a valid physical quantity."})
    if not qty.is_finite() or qty < 0 or qty.as_tuple().exponent < -2 or qty > Decimal("99999999.99"):
        raise ValidationError({"countedQty": "Physical quantity must be nonnegative with at most two decimals."})
    key = stock_request_key(request)
    if not key:
        raise ValidationError({"requestKey": "A unique physical-count request key is required."})
    reason = str(request.data.get("reason") or "").strip()
    if len(reason) < 5:
        raise ValidationError({"reason": "Record why the counted quantity differs from the system balance."})
    Company.objects.select_for_update().get(pk=company.pk)
    existing = StockAudit.objects.filter(company=company, request_key=key).first()
    if existing:
        if str(existing.item_id) != str(request.data.get("item") or request.data.get("itemId")) or existing.counted_qty != qty:
            raise ValidationError({"requestKey": "This physical count key was used for another count."})
        return existing, False
    try:
        item = StockItem.objects.select_for_update().get(
            company=company, pk=request.data.get("item") or request.data.get("itemId"),
        )
    except (StockItem.DoesNotExist, ValueError, TypeError):
        raise ValidationError({"item": "Choose a workshop stock item."})
    branch = request.user.branch
    row = locked_branch_balance(item, branch)
    delta = qty - row.quantity
    # A physical count must not silently consume reserved job-card stock.
    if qty < row.reserved:
        raise ValidationError({"countedQty": "Cannot count below reserved stock; reconcile reservations first."})
    move_balance(row, delta)
    item.on_hand += delta
    item.save(update_fields=["on_hand", "updated_at"])
    audit = StockAudit.objects.create(
        company=company, branch=branch, item=item,
        request_key=key, expected_qty=qty - delta, counted_qty=qty,
        difference=delta, reason=reason[:255], created_by=request.user,
    )
    if delta:
        StockMovement.objects.create(
            company=company, branch=branch, item=item,
            movement_type="adjustment", quantity=delta,
            unit_cost=item.cost_price, unit_selling_price=item.selling_price,
            reference="COUNT:" + str(audit.pk), note=reason[:2000],
            created_by=request.user,
        )
    return audit, True
