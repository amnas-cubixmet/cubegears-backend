from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.accounts.permissions import user_has_permission
from apps.inventory.models import StockItem, StockMovement
from apps.inventory.branch_stock import locked_branch_balance, move_balance
from apps.jobs.audit import record_job_event
from .template_settings import template_for_document


def finalize_invoice(obj, user):
    """Issue a document inside the caller's transaction and invoice lock."""
    if obj.status in {"Finalized", "Paid", "Issued", "Converted"}:
        return
    if obj.status != "Draft":
        raise ValidationError({"status": "Only draft documents can be finalized."})
    if obj.kind=="invoice":
        # Only invoices that consume stock require the additional Stock permission.
        if obj.items.filter(stock_item__isnull=False).exists() and not user_has_permission(user, "stock.edit"):
            raise PermissionDenied("Stock edit permission is required to finalize invoices containing stock items.")
        expected_type = "gst" if str(obj.company.gstin or "").strip() else "regular"
        if obj.invoice_type != expected_type:
            raise ValidationError({
                "invoiceType": "Invoice type does not match workshop GST registration. Update Company Settings and recreate the draft as needed."
            })
        if obj.invoice_type == "gst":
            lines = list(obj.items.all())
            if not lines or any(not line.hsn_sac.strip() for line in lines):
                raise ValidationError({"items": "Classify every GST invoice item with its applicable HSN/SAC before finalizing."})
        for line in obj.items.select_related("stock_item"):
            if not line.stock_item_id: continue
            try:
                item=StockItem.objects.select_for_update().get(pk=line.stock_item_id,company=obj.company)
            except StockItem.DoesNotExist:
                raise ValidationError("An invoice item references stock from another company or a missing stock item.")
            if obj.branch_id:
                branch_balance = locked_branch_balance(item, obj.branch)
                move_balance(branch_balance, -line.quantity)
            elif item.available_quantity < line.quantity:
                raise ValidationError(f"Insufficient stock for {item.name}.")
            item.on_hand -= line.quantity
            item.save(update_fields=["on_hand", "updated_at"])
            StockMovement.objects.create(
                company=obj.company, branch=obj.branch, item=item,
                movement_type="sale", quantity=-line.quantity,
                unit_cost=item.cost_price, unit_selling_price=line.rate,
                reference=obj.number, created_by=user,
            )
        obj.status="Paid" if obj.balance<=0 else "Finalized"
    else: obj.status="Issued"
    if not obj.template_snapshot:
        obj.template_snapshot = template_for_document(obj.company, obj.kind)
    obj.finalized_at=timezone.now()
    obj.save(update_fields=["status","finalized_at","template_snapshot","updated_at"])
    if obj.job_id:
        record_job_event(obj.job, user, "Invoice Finalized",
                         f"{obj.number} {obj.status.lower()}", {"invoiceId": str(obj.pk)})
