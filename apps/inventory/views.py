from django.db import transaction
from django.db.models import F,Q
from rest_framework import decorators,response,status
from common.viewsets import CompanyScopedModelViewSet
from .models import StockCategory,Supplier,StockItem,StockMovement,PurchaseOrder,StockTransfer,StockAudit
from .serializers import *

class StockItemViewSet(CompanyScopedModelViewSet):
    queryset=StockItem.objects.select_related("category","supplier").all(); serializer_class=StockItemSerializer
    def get_queryset(self):
        qs=super().get_queryset(); q=self.request.query_params.get("search")
        if q: qs=qs.filter(Q(name__icontains=q)|Q(sku__icontains=q)|Q(barcode__icontains=q)|Q(brand__icontains=q))
        if self.request.query_params.get("low_stock") in {"1","true","True"}: qs=qs.filter(on_hand__lte=F("minimum_stock")+F("reserved"))
        return qs
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
