from django.db import transaction
from django.db.models import F,Q
from rest_framework import decorators,response,status
from common.viewsets import CompanyScopedModelViewSet
from .models import StockCategory,Supplier,StockItem,StockMovement,PurchaseOrder,StockTransfer,StockAudit
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
    }
    def get_queryset(self):
        qs=super().get_queryset(); q=self.request.query_params.get("search")
        if q: qs=qs.filter(Q(name__icontains=q)|Q(sku__icontains=q)|Q(barcode__icontains=q)|Q(brand__icontains=q))
        if self.request.query_params.get("low_stock") in {"1","true","True"}: qs=qs.filter(on_hand__lte=F("minimum_stock")+F("reserved"))
        return qs

    @transaction.atomic
    def perform_create(self, serializer):
        super().perform_create(serializer)
        item = serializer.instance
        if item.on_hand > 0:
            StockMovement.objects.create(
                company=item.company,
                branch=item.branch,
                item=item,
                movement_type="opening",
                quantity=item.on_hand,
                unit_cost=item.cost_price,
                note="Initial stock entered when the part was created.",
                created_by=self.request.user,
            )

    def perform_update(self, serializer):
        item = serializer.instance
        next_qty = serializer.validated_data.get("on_hand", item.on_hand)
        if next_qty != item.on_hand:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({"onHand": "Use Stock In, Stock Out, or Adjustment to change quantity."})
        serializer.save()

    @decorators.action(detail=False,methods=["get"])
    def dashboard(self,request):
        from django.db.models import Sum
        qs=self.get_queryset()
        total_value=sum(float(x.on_hand*x.cost_price) for x in qs)
        return response.Response({
            "totalItems":qs.count(),
            "totalValue":total_value,
            "lowStock":qs.filter(on_hand__lte=F("minimum_stock")+F("reserved"),on_hand__gt=0).count(),
            "outOfStock":qs.filter(on_hand__lte=0).count(),
            "reservedStock":qs.aggregate(v=Sum("reserved"))["v"] or 0,
            "issuedToday":StockMovement.objects.filter(company=request.user.company,movement_type__in=["job_issue","sale"],created_at__date=__import__("django.utils.timezone",fromlist=["localdate"]).localdate()).count(),
            "receivedToday":StockMovement.objects.filter(company=request.user.company,movement_type="purchase",created_at__date=__import__("django.utils.timezone",fromlist=["localdate"]).localdate()).count(),
            "pendingPurchases":PurchaseOrder.objects.filter(company=request.user.company,status__in=["Draft","Ordered","Partially Received"]).count(),
        })

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
        try:
            item = get_object_or_404(
                StockItem.objects.select_for_update(),
                pk=item_id,
                company=request.user.company,
            )
        except (ValueError, DjangoValidationError):
            return response.Response({"message": "Invalid stock item."}, status=400)

        user_branch_id = getattr(request.user, "branch_id", None)
        if item.branch_id is not None and item.branch_id != user_branch_id:
            return response.Response({"message": "This part belongs to another branch."}, status=403)

        delta = qty * sign
        if delta < 0 and item.available_quantity < qty:
            return response.Response({"message": "Insufficient available stock."}, status=400)
        if item.on_hand + delta < 0:
            return response.Response({"message": "Stock cannot be negative."}, status=400)

        item.on_hand += delta
        item.save(update_fields=["on_hand", "updated_at"])
        movement = StockMovement.objects.create(
            company=item.company,
            branch=item.branch or request.user.branch,
            item=item,
            movement_type=movement_type,
            quantity=delta,
            unit_cost=item.cost_price,
            reference=str(request.data.get("jobId") or request.data.get("invoiceNo") or "")[:100],
            note=request.data.get("notes") or "",
            created_by=request.user,
        )
        return response.Response(StockMovementSerializer(movement).data, status=201)

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
        try:
            item = get_object_or_404(
                StockItem.objects.select_for_update(),
                pk=item_id, company=request.user.company,
            )
        except (ValueError, DjangoValidationError):
            return response.Response({"message": "Invalid product."}, status=400)
        if item.branch_id is not None and item.branch_id != getattr(request.user, "branch_id", None):
            return response.Response({"message": "This product belongs to another branch."}, status=403)

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

        movement = StockMovement.objects.create(
            company=item.company,
            branch=item.branch or request.user.branch,
            item=item,
            movement_type="purchase",
            quantity=quantity,
            unit_cost=purchase_cost,
            unit_selling_price=selling_price,
            reference=str(request.data.get("invoiceNo") or "")[:100],
            note=request.data.get("notes") or "",
            created_by=request.user,
        )
        result = StockMovementSerializer(movement).data
        result["pricesUpdated"] = mode == "apply_new" and prices_changed
        result["catalogCostPrice"] = str(item.cost_price)
        result["catalogSellingPrice"] = str(item.selling_price)
        return response.Response(result, status=201)

    @decorators.action(detail=False,methods=["post"])
    def issue(self,request): return self._movement(request,"sale",-1)

    @decorators.action(detail=False,methods=["post"],url_path="return")
    def stock_return(self,request):
        condition=request.data.get("condition","Reusable")
        return self._movement(request,"return",1 if condition=="Reusable" else 0)

    @decorators.action(detail=False,methods=["post"])
    def adjustment(self,request):
        sign=1 if request.data.get("adjustmentType","Increase")=="Increase" else -1
        return self._movement(request,"adjustment",sign)

    @decorators.action(detail=False,methods=["get"])
    def ledger(self,request):
        return response.Response(StockMovementSerializer(StockMovement.objects.filter(company=request.user.company).select_related("item"),many=True).data)

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
    def transfer(self,request):
        from apps.branches.models import Branch
        company=request.user.company
        from_value=request.data.get("fromBranch")
        to_value=request.data.get("toBranch")
        from_branch=Branch.objects.filter(company=company,name=from_value).first() or request.user.branch
        to_branch=Branch.objects.filter(company=company,name=to_value).first()
        if not from_branch or not to_branch:
            return response.Response({"message":"Valid from/to branches are required."},status=400)
        obj=StockTransfer.objects.create(company=company,branch=request.user.branch,number=f"TRF-{__import__('time').time_ns()}",from_branch=from_branch,to_branch=to_branch,status="Requested",items=[request.data])
        return response.Response(StockTransferSerializer(obj).data,status=201)

    @decorators.action(detail=False,methods=["get"],url_path="low-stock")
    def low_stock(self,request):
        qs=self.get_queryset().filter(on_hand__lte=F("minimum_stock")+F("reserved"))
        return response.Response(self.get_serializer(qs,many=True).data)

class StockCategoryViewSet(CompanyScopedModelViewSet):
    queryset=StockCategory.objects.all(); serializer_class=StockCategorySerializer
class SupplierViewSet(CompanyScopedModelViewSet):
    queryset=Supplier.objects.all(); serializer_class=SupplierSerializer
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
class StockTransferViewSet(CompanyScopedModelViewSet):
    queryset=StockTransfer.objects.all(); serializer_class=StockTransferSerializer
class StockAuditViewSet(CompanyScopedModelViewSet):
    queryset=StockAudit.objects.all(); serializer_class=StockAuditSerializer
    def perform_create(self,serializer):
        item=serializer.validated_data["item"]; counted=serializer.validated_data["counted_qty"]
        serializer.save(company=self.request.user.company,branch=self.request.user.branch,expected_qty=item.on_hand,difference=counted-item.on_hand,created_by=self.request.user)
