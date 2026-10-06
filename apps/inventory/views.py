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

    def _movement(self,request,movement_type,sign=1):
        item_id=request.data.get("itemId") or request.data.get("item")
        item=StockItem.objects.get(pk=item_id,company=request.user.company)
        qty=float(request.data.get("quantity",1))
        if qty<=0: return response.Response({"message":"Quantity must be greater than zero."},status=400)
        delta=qty*sign
        if delta<0 and float(item.available_quantity)<qty:
            return response.Response({"message":"Insufficient stock."},status=400)
        item.on_hand=float(item.on_hand)+delta
        item.save(update_fields=["on_hand","updated_at"])
        mv=StockMovement.objects.create(company=request.user.company,branch=request.user.branch,item=item,movement_type=movement_type,quantity=delta,reference=request.data.get("jobId") or request.data.get("invoiceNo") or "",note=request.data.get("notes") or "",created_by=request.user)
        return response.Response(StockMovementSerializer(mv).data,status=201)

    @decorators.action(detail=False,methods=["post"],url_path="in")
    def stock_in(self,request): return self._movement(request,"purchase",1)

    @decorators.action(detail=False,methods=["post"])
    def issue(self,request): return self._movement(request,"job_issue",-1)

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
