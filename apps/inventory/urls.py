from rest_framework.routers import DefaultRouter
from .views import *
router=DefaultRouter(trailing_slash=False)
router.register("categories",StockCategoryViewSet,basename="stock-categories")
router.register("suppliers",SupplierViewSet,basename="stock-suppliers")
router.register("movements",StockMovementViewSet,basename="stock-movements")
router.register("purchase-orders",PurchaseOrderViewSet,basename="purchase-orders")
router.register("transfers",StockTransferViewSet,basename="stock-transfers")
router.register("audit",StockAuditViewSet,basename="stock-audit")
router.register("items",StockItemViewSet,basename="stock-items")
router.register("",StockItemViewSet,basename="stock")
urlpatterns=router.urls
