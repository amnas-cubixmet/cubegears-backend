"""Independent supplier APIs. Legacy /stock/procurement remains available."""
from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import SupplierViewSet
from .finance_api import (
    AccountCollection, AccountLedger,
    SupplierBillCollection, SupplierBillPost,
    SupplierPaymentCollection, SupplierAdvanceApply,
    SupplierSummary, SupplierLedger, SupplierPurchaseGSTOptions,
)

router = DefaultRouter(trailing_slash=False)
router.register("", SupplierViewSet, basename="suppliers")

urlpatterns = [
    path("procurement/gst-options", SupplierPurchaseGSTOptions.as_view(), name="suppliers-gst-options"),
    path("procurement/accounts", AccountCollection.as_view(), name="suppliers-workshop-accounts"),
    path("procurement/accounts/<uuid:pk>/ledger", AccountLedger.as_view(), name="suppliers-workshop-account-ledger"),
    path("procurement/bills", SupplierBillCollection.as_view(), name="suppliers-bills"),
    path("procurement/bills/<uuid:pk>/post", SupplierBillPost.as_view(), name="suppliers-bill-post"),
    path("procurement/payments", SupplierPaymentCollection.as_view(), name="suppliers-payments"),
    path("procurement/payments/<uuid:pk>/allocate", SupplierAdvanceApply.as_view(), name="suppliers-advance-allocate"),
    path("<uuid:pk>/summary", SupplierSummary.as_view(), name="suppliers-summary"),
    path("<uuid:pk>/ledger", SupplierLedger.as_view(), name="suppliers-ledger"),
] + router.urls
