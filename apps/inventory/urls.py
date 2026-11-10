from django.urls import path
from apps.suppliers.returns_api import SupplierDebitNotes, SupplierDebitApply, SupplierRefunds, SupplierPayables
from apps.suppliers.balances_api import SupplierBalanceCollection
from .supplier_finance_api import (
    AccountCollection, AccountLedger, SupplierBillCollection, SupplierBillPost,
    SupplierPaymentCollection, SupplierAdvanceApply, SupplierSummary, SupplierLedger,
    SupplierPurchaseGSTOptions,
)
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
urlpatterns = [
    path("procurement/suppliers/balances", SupplierBalanceCollection.as_view(), name="inventory-supplier-balances"),
    path("procurement/gst-options", SupplierPurchaseGSTOptions.as_view(), name="inventory-gst-options"),
    path("procurement/accounts", AccountCollection.as_view(), name="workshop-accounts"),
    path("procurement/accounts/<uuid:pk>/ledger", AccountLedger.as_view(), name="workshop-account-ledger"),
    path("procurement/bills", SupplierBillCollection.as_view(), name="supplier-bills"),
    path("procurement/debit-notes", SupplierDebitNotes.as_view(), name="supplier-debit-notes"),
    path("procurement/debit-notes/<uuid:pk>/apply", SupplierDebitApply.as_view(), name="supplier-debit-apply"),
    path("procurement/refunds", SupplierRefunds.as_view(), name="supplier-refunds"),
    path("procurement/payables", SupplierPayables.as_view(), name="supplier-payables"),
    path("procurement/bills/<uuid:pk>/post", SupplierBillPost.as_view(), name="supplier-bill-post"),
    path("procurement/payments", SupplierPaymentCollection.as_view(), name="supplier-payments"),
    path("procurement/payments/<uuid:pk>/allocate", SupplierAdvanceApply.as_view(), name="supplier-advance-apply"),
    path("procurement/suppliers/<uuid:pk>/summary", SupplierSummary.as_view(), name="supplier-financial-summary"),
    path("procurement/suppliers/<uuid:pk>/ledger", SupplierLedger.as_view(), name="supplier-financial-ledger"),
] + router.urls
