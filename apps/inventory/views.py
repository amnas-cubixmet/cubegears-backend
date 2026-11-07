import re
from django.db import transaction
from django.db.models import F,Q
from rest_framework import decorators,response
from rest_framework.exceptions import ValidationError
from apps.companies.models import Company
from common.viewsets import CompanyScopedModelViewSet
from .models import StockCategory,StockItem,StockMovement,PurchaseOrder,StockTransfer,StockAudit,StockBranchBalance
from .branch_stock import locked_branch_balance, move_balance, available_in_branch
from .request_keys import stock_request_key, replay_movement
from .part12_operations import transfer_inventory, return_issued_stock, count_inventory
from .serializers import *

class StockItemViewSet(CompanyScopedModelViewSet):
    queryset=StockItem.objects.select_related("category","supplier").all(); serializer_class=StockItemSerializer
    action_permission_map = {
        "dashboard": "stock.view",
        "stock_in": "stock.create",
        "issue": "stock.edit",
        "stock_return": "stock.edit",
        "adjustment": "stock.edit",
        "ledger": "stock.view",
        "purchases": "stock.view",
        "reservations": "stock.view",
        "counts": "stock.view",
        "transfer": "stock.edit",
        "low_stock": "stock.view",
        "branch_balances": "stock.view",
    }
    def get_queryset(self):
        qs=super().get_queryset(); q=self.request.query_params.get("search")
        if q: qs=qs.filter(Q(name__icontains=q)|Q(sku__icontains=q)|Q(part_number__icontains=q)|Q(barcode__icontains=q)|Q(brand__icontains=q)|Q(rack__icontains=q)|Q(category__name__icontains=q)|Q(supplier__name__icontains=q))
        if self.request.query_params.get("low_stock") in {"1","true","True"}: qs=qs.filter(on_hand__lte=F("minimum_stock")+F("reserved"))
        return qs

    @decorators.action(detail=False, methods=["get"], url_path="rack-locations")
    def rack_locations(self, request):
        """Persistent suggestions from existing stock, never from unfinished typing."""
        qs = StockItem.objects.filter(company_id=request.user.company_id)
        branch_id = getattr(request.user, "branch_id", None)
        if branch_id:
            qs = qs.filter(Q(branch_id=branch_id) | Q(branch__isnull=True))
        term = request.query_params.get("q", "").strip()[:80]
        if term:
            qs = qs.filter(rack__icontains=term)
        seen = set()
        locations = []
        for rack in qs.exclude(rack="").order_by("rack").values_list("rack", flat=True)[:500]:
            name = rack.strip()
            if not name or name.casefold() in seen:
                continue
            seen.add(name.casefold())
            locations.append(name)
            if len(locations) >= 80:
                break
        return response.Response(locations)

    @transaction.atomic
    def perform_create(self, serializer):
        # Serialize SKU assignment for this tenant (including manual SKUs).
        # This also prevents races between concurrently generated values.
        Company.objects.select_for_update().get(pk=self.request.user.company_id)
        sku = str(serializer.validated_data.get("sku") or "").strip()
        if not sku:
            last = (StockItem.objects.filter(
                company_id=self.request.user.company_id, sku__startswith="CG-PRT-",
            ).order_by("-sku").values_list("sku", flat=True).first())
            number = int(last[7:]) + 1 if last and re.fullmatch(r"CG-PRT-\d{6,}", last) else 1
            sku = f"CG-PRT-{number:06d}"
            while StockItem.objects.filter(company_id=self.request.user.company_id, sku__iexact=sku).exists():
                number += 1
                sku = f"CG-PRT-{number:06d}"
        if StockItem.objects.filter(company_id=self.request.user.company_id, sku__iexact=sku).exists():
            raise ValidationError({"sku": "This SKU already exists in your company."})
        serializer.validated_data["sku"] = sku
        super().perform_create(serializer)
        item = serializer.instance
        if item.branch_id:
            StockBranchBalance.objects.create(
                company=item.company, branch=item.branch, item=item,
                quantity=item.on_hand, reserved=item.reserved,
            )
        if item.on_hand > 0:
            StockMovement.objects.create(
                company=item.company,
                branch=item.branch,
                item=item,
                movement_type="opening",
                quantity=item.on_hand,
                unit_cost=item.cost_price,
                unit_selling_price=item.selling_price,
                note="Opening inventory; this entry does not create a supplier bill or payment.",
                created_by=self.request.user,
            )

    def perform_update(self, serializer):
        item = serializer.instance
        next_qty = serializer.validated_data.get("on_hand", item.on_hand)
        if serializer.validated_data.get("reserved", item.reserved) != item.reserved:
            raise ValidationError({"reserved": "Reservations must be changed through the job allocation workflow."})
        if next_qty != item.on_hand:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({"onHand": "Use Stock In, Stock Out, or Adjustment to change quantity."})
        serializer.save()

    def destroy(self, request, *args, **kwargs):
        # Historic stock ledger and invoices continue to refer to the same SKU.
        item = self.get_object()
        item.status = "Inactive"
        item.save(update_fields=["status", "updated_at"])
        return response.Response({"id": str(item.pk), "status": "Inactive"})

    @decorators.action(detail=False,methods=["get"])
    def dashboard(self,request):
        from django.db.models import Sum
        qs=self.get_queryset()
        from decimal import Decimal
        from apps.accounts.permissions import user_has_permission
        can_view_cost = user_has_permission(request.user, "stock.edit")
        total_value = sum((x.on_hand * x.cost_price for x in qs), Decimal("0")) if can_view_cost else None
        return response.Response({
            "totalItems":qs.count(),
            **({"totalValue": str(total_value)} if can_view_cost else {}),
            "lowStock":qs.filter(on_hand__lte=F("minimum_stock")+F("reserved"),on_hand__gt=0).count(),
            "outOfStock":qs.filter(on_hand__lte=0).count(),
            "reservedStock":qs.aggregate(v=Sum("reserved"))["v"] or 0,
            "issuedToday":StockMovement.objects.filter(company=request.user.company,movement_type__in=["job_issue","sale"],created_at__date=__import__("django.utils.timezone",fromlist=["localdate"]).localdate()).count(),
            "receivedToday":StockMovement.objects.filter(company=request.user.company,movement_type="purchase",created_at__date=__import__("django.utils.timezone",fromlist=["localdate"]).localdate()).count(),
            "pendingPurchases":PurchaseOrder.objects.filter(company=request.user.company,status__in=["Draft","Ordered","Partially Received"]).count(),
        })

    def _movement_response(self, request, movement):
        return StockMovementSerializer(movement, context={"request": request}).data

    @transaction.atomic
    def _movement(self, request, movement_type, sign=1):
        """The inventory balance and its ledger entry must commit together."""
        from decimal import Decimal, InvalidOperation
        from django.shortcuts import get_object_or_404
        from django.core.exceptions import ValidationError as DjangoValidationError

        item_id = request.data.get("itemId") or request.data.get("item")
        if not item_id:
            return response.Response({"message": "Select a stock item."}, status=400)
        try:
            qty = Decimal(str(request.data.get("quantity", "1")))
        except (InvalidOperation, ValueError, TypeError):
            return response.Response({"message": "Enter a valid quantity."}, status=400)
        if not qty.is_finite() or qty <= 0 or qty.as_tuple().exponent < -2:
            return response.Response({"message": "Quantity must be positive, with at most two decimal places."}, status=400)
        key = stock_request_key(request)
        Company.objects.select_for_update().get(pk=request.user.company_id)
        try:
            item = get_object_or_404(
                StockItem.objects.select_for_update(),
                pk=item_id,
                company=request.user.company,
            )
        except (ValueError, DjangoValidationError):
            return response.Response({"message": "Invalid stock item."}, status=400)

        delta = qty * sign
        existing = replay_movement(request, key, item=item, kind=movement_type, quantity=delta)
        if existing:
            return response.Response(self._movement_response(request, existing), status=200)
        balance = locked_branch_balance(item, request.user.branch)
        move_balance(balance, delta)
        item.on_hand += delta
        item.save(update_fields=["on_hand", "updated_at"])
        movement = StockMovement.objects.create(
            company=item.company,
            branch=request.user.branch,
            item=item, request_key=key,
            movement_type=movement_type,
            quantity=delta,
            unit_cost=item.cost_price,
            reference=str(request.data.get("jobId") or request.data.get("invoiceNo") or "")[:100],
            note=request.data.get("notes") or "",
            created_by=request.user,
        )
        return response.Response(self._movement_response(request, movement), status=201)

    @decorators.action(detail=False, methods=["post"], url_path="in")
    @transaction.atomic
    def stock_in(self, request):
        """Receive goods at their actual cost; change catalogue prices only by explicit choice.

        One SKU has one current catalogue selling price. New receipt prices are stored
        as movement snapshots even when catalogue prices are intentionally retained.
        """
        from decimal import Decimal, InvalidOperation
        from django.shortcuts import get_object_or_404
        from django.core.exceptions import ValidationError as DjangoValidationError
        from apps.accounts.permissions import user_has_permission

        item_id = request.data.get("itemId") or request.data.get("item")
        if not item_id:
            return response.Response({"message": "Select an existing product."}, status=400)
        key = stock_request_key(request)
        Company.objects.select_for_update().get(pk=request.user.company_id)
        try:
            item = get_object_or_404(
                StockItem.objects.select_for_update(),
                pk=item_id, company=request.user.company,
            )
        except (ValueError, DjangoValidationError):
            return response.Response({"message": "Invalid product."}, status=400)
        balance = locked_branch_balance(item, request.user.branch)

        def money_field(value, label):
            try:
                result = Decimal(str(value))
            except (InvalidOperation, ValueError, TypeError):
                raise ValueError(label + " must be a valid amount.")
            if not result.is_finite() or result < 0 or result.as_tuple().exponent < -2 or result >= Decimal("10000000000"):
                raise ValueError(label + " must be nonnegative, with at most two decimals.")
            return result

        try:
            quantity = money_field(request.data.get("quantity"), "Quantity")
            if quantity <= 0:
                return response.Response({"message": "Quantity must be greater than zero."}, status=400)
            purchase_cost = money_field(
                request.data.get("costPrice", item.cost_price), "Purchase cost")
            selling_price = money_field(
                request.data.get("sellingPrice", item.selling_price), "Selling price")
        except ValueError as exc:
            return response.Response({"message": str(exc)}, status=400)

        prior = replay_movement(request, key, item=item, kind="purchase", quantity=quantity)
        if prior:
            if prior.unit_cost != purchase_cost or prior.unit_selling_price != selling_price:
                raise ValidationError({"requestKey": "This key belongs to an earlier receipt with different prices."})
            return response.Response(self._movement_response(request, prior), status=200)
        supplier_bill_id = request.data.get("supplierBillId")
        if supplier_bill_id:
            from .models import SupplierBill
            try:
                already_received = SupplierBill.objects.filter(
                    company=request.user.company, pk=supplier_bill_id,
                    status="Posted", receive_into_stock=True,
                ).exists()
            except (ValueError, TypeError):
                raise ValidationError({"supplierBillId": "Select a valid supplier bill."})
            if already_received:
                raise ValidationError({"supplierBillId": "Stock was already received when this supplier bill was posted."})
        prices_changed = purchase_cost != item.cost_price or selling_price != item.selling_price
        mode = request.data.get("priceMode") or ""
        if mode and mode not in {"apply_new", "keep_existing"}:
            return response.Response({"message": "Invalid price choice."}, status=400)
        if prices_changed and mode not in {"apply_new", "keep_existing"}:
            return response.Response({
                "message": "Prices differ from existing stock. Choose whether to apply the new prices to all units.",
                "priceChoiceRequired": True,
            }, status=400)
        if mode == "apply_new" and prices_changed and not user_has_permission(request.user, "stock.edit"):
            return response.Response({
                "message": "Stock edit permission is required to change catalogue prices."
            }, status=403)

        if mode == "apply_new":
            item.cost_price = purchase_cost
            item.selling_price = selling_price
            item.on_hand += quantity
            item.save(update_fields=["cost_price", "selling_price", "on_hand", "updated_at"])
        else:
            item.on_hand += quantity
            item.save(update_fields=["on_hand", "updated_at"])

        move_balance(balance, quantity)
        movement = StockMovement.objects.create(
            company=item.company,
            branch=request.user.branch,
            item=item, request_key=key,
            movement_type="purchase",
            quantity=quantity,
            unit_cost=purchase_cost,
            unit_selling_price=selling_price,
            reference=str(request.data.get("invoiceNo") or "")[:100],
            note=request.data.get("notes") or "",
            created_by=request.user,
        )
        result = self._movement_response(request, movement)
        result["pricesUpdated"] = mode == "apply_new" and prices_changed
        if user_has_permission(request.user, "stock.edit"):
            result["catalogCostPrice"] = str(item.cost_price)
        result["catalogSellingPrice"] = str(item.selling_price)
        return response.Response(result, status=201)

    @decorators.action(detail=False,methods=["post"])
    def issue(self,request): return self._movement(request,"sale",-1)

    @decorators.action(detail=False,methods=["post"],url_path="return")
    def stock_return(self,request):
        movement, created = return_issued_stock(request)
        return response.Response(self._movement_response(request, movement), status=201 if created else 200)

    @decorators.action(detail=False,methods=["post"])
    def adjustment(self,request):
        mode = str(request.data.get("adjustmentType") or "")
        if mode not in {"Increase", "Decrease"}:
            raise ValidationError({"adjustmentType": "Choose Increase or Decrease."})
        if len(str(request.data.get("reason") or "").strip()) < 5:
            raise ValidationError({"reason": "Explain this adjustment (minimum 5 characters)."})
        sign = 1 if mode == "Increase" else -1
        return self._movement(request,"adjustment",sign)

    @decorators.action(detail=False,methods=["get"],url_path="branch-balances")
    def branch_balances(self, request):
        from apps.accounts.permissions import user_has_permission
        qs = StockBranchBalance.objects.filter(company=request.user.company).select_related("item","branch")
        if request.user.branch_id and not user_has_permission(request.user, "stock.manage"):
            qs = qs.filter(branch_id=request.user.branch_id)
        return response.Response([{
            "itemId": str(row.item_id), "sku": row.item.sku,
            "name": row.item.name, "branchId": str(row.branch_id),
            "branchName": row.branch.name, "onHand": str(row.quantity),
            "reserved": str(row.reserved), "available": str(available_in_branch(row)),
        } for row in qs.order_by("branch__name", "item__name")[:2000]])

    @decorators.action(detail=False,methods=["get"])
    def ledger(self,request):
        return response.Response(StockMovementSerializer(StockMovement.objects.filter(company=request.user.company).select_related("item"),many=True,context={"request": request}).data)

    @decorators.action(detail=False,methods=["get"])
    def purchases(self,request):
        return response.Response(PurchaseOrderSerializer(PurchaseOrder.objects.filter(company=request.user.company),many=True).data)

    @decorators.action(detail=False,methods=["get"])
    def reservations(self,request):
        return response.Response([])

    @decorators.action(detail=False,methods=["get"])
    def counts(self,request):
        return response.Response(StockAuditSerializer(StockAudit.objects.filter(company=request.user.company),many=True).data)

    @decorators.action(detail=False,methods=["post"])
    def transfer(self, request):
        transfer, created = transfer_inventory(request)
        return response.Response(StockTransferSerializer(transfer).data, status=201 if created else 200)

    @decorators.action(detail=False,methods=["get"],url_path="low-stock")
    def low_stock(self,request):
        qs=self.get_queryset().filter(on_hand__lte=F("minimum_stock")+F("reserved"))
        return response.Response(self.get_serializer(qs,many=True).data)

class StockCategoryViewSet(CompanyScopedModelViewSet):
    queryset=StockCategory.objects.all(); serializer_class=StockCategorySerializer
# Backwards-compatible inventory URL alias for canonical supplier CRUD.
from apps.suppliers.views import SupplierViewSet as SupplierViewSet

class StockMovementViewSet(CompanyScopedModelViewSet):
    queryset=StockMovement.objects.select_related("item").all(); serializer_class=StockMovementSerializer
    def create(self, request, *args, **kwargs):
        return response.Response(
            {"message": "Use Stock In, Internal Use, Return, or Adjustment actions to record movements."},
            status=405,
        )
    def update(self, request, *args, **kwargs):
        return response.Response({"message": "Stock movements are immutable."}, status=405)
    def partial_update(self, request, *args, **kwargs):
        return response.Response({"message": "Stock movements are immutable."}, status=405)
    def destroy(self, request, *args, **kwargs):
        return response.Response({"message": "Stock movements are immutable."}, status=405)

    def perform_create(self,serializer):
        with transaction.atomic():
            item=StockItem.objects.select_for_update().get(pk=self.request.data["item"],company=self.request.user.company)
            qty=serializer.validated_data["quantity"]; item.on_hand=F("on_hand")+qty; item.save(update_fields=["on_hand","updated_at"]); item.refresh_from_db()
            serializer.save(company=self.request.user.company,branch=self.request.user.branch,created_by=self.request.user)
class PurchaseOrderViewSet(CompanyScopedModelViewSet):
    queryset=PurchaseOrder.objects.all(); serializer_class=PurchaseOrderSerializer

    def perform_update(self, serializer):
        from rest_framework.exceptions import ValidationError
        if hasattr(serializer.instance, "supplier_credit"):
            protected = {"total", "subtotal", "tax", "supplier", "items", "status", "number"}
            if protected.intersection(serializer.validated_data):
                raise ValidationError({"message": "A purchase with supplier credit cannot have its financial fields changed."})
        serializer.save()

    def perform_destroy(self, instance):
        from rest_framework.exceptions import ValidationError
        if hasattr(instance, "supplier_credit"):
            raise ValidationError({"message": "Cannot delete a purchase with linked supplier credit."})
        instance.delete()
class StockTransferViewSet(CompanyScopedModelViewSet):
    queryset=StockTransfer.objects.all(); serializer_class=StockTransferSerializer

    def create(self, request, *args, **kwargs):
        return response.Response({"message": "Use the atomic /stock/transfer action."}, status=405)

    def update(self, request, *args, **kwargs):
        return response.Response({"message": "Stock transfer history is immutable."}, status=405)

    def partial_update(self, request, *args, **kwargs):
        return self.update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        return response.Response({"message": "Stock transfers cannot be deleted."}, status=405)

class StockAuditViewSet(CompanyScopedModelViewSet):
    queryset=StockAudit.objects.all(); serializer_class=StockAuditSerializer
    action_permission_map = {"create": "stock.edit"}

    def create(self, request, *args, **kwargs):
        audit, created = count_inventory(request)
        return response.Response(self.get_serializer(audit).data, status=201 if created else 200)

    def update(self, request, *args, **kwargs):
        return response.Response({"message": "Physical count history is immutable."}, status=405)

    def partial_update(self, request, *args, **kwargs):
        return self.update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        return response.Response({"message": "Physical count history cannot be deleted."}, status=405)
