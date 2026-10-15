from django.urls import path
from .bundle_views import BundleListView, BundlePurchaseView, BundleDisableView
from .gateway_diagnostics import GatewayStatusView, GatewayTestView
from .views import (
    CompanyCreditsView, CreditPackagesView, CreditFeaturesView,
    CreditLogsView, CreateCreditOrderView, VerifyCreditPaymentView,
    EnableCreditFeatureView, DisableCreditFeatureView, CreditWebhookView,
)

urlpatterns = [
    path("overview", CompanyCreditsView.as_view(), name="credit-overview"),
    path("gateway/status", GatewayStatusView.as_view(), name="credit-gateway-status"),
    path("gateway/test", GatewayTestView.as_view(), name="credit-gateway-test"),
    path("packages", CreditPackagesView.as_view(), name="credit-packages"),
    path("bundles", BundleListView.as_view(), name="credit-bundles"),
    path("bundles/purchase", BundlePurchaseView.as_view(), name="credit-bundle-purchase"),
    path("bundles/disable", BundleDisableView.as_view(), name="credit-bundle-disable"),
    path("features", CreditFeaturesView.as_view(), name="credit-features"),
    path("logs", CreditLogsView.as_view(), name="credit-logs"),
    path("orders", CreateCreditOrderView.as_view(), name="credit-orders"),
    path("verify", VerifyCreditPaymentView.as_view(), name="credit-verify"),
    path("features/enable", EnableCreditFeatureView.as_view(), name="credit-feature-enable"),
    path("features/disable", DisableCreditFeatureView.as_view(), name="credit-feature-disable"),
    path("webhook", CreditWebhookView.as_view(), name="credit-razorpay-webhook"),
]
