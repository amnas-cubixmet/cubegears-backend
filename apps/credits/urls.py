from django.urls import path
from .hosted_views import HostedCreditCheckoutView, HostedCreditStatusView
from .bundle_views import BundleListView, BundlePurchaseView, BundleDisableView
from .entitlement_views import CompanyModuleEntitlementsView
from .access_views import TenantFeatureAccessView
from .usage_views import CompanyUsageInvoicesView
from .gateway_diagnostics import GatewayStatusView, GatewayTestView
from .views import (
    CompanyCreditsView, CreditPackagesView, CreditFeaturesView,
    CreditLogsView, CreateCreditOrderView, VerifyCreditPaymentView,
    EnableCreditFeatureView, DisableCreditFeatureView, CreditWebhookView,
)

urlpatterns = [
    path("overview", CompanyCreditsView.as_view(), name="credit-overview"),
    path("access", TenantFeatureAccessView.as_view(), name="credit-access"),
    path("gateway/status", GatewayStatusView.as_view(), name="credit-gateway-status"),
    path("gateway/test", GatewayTestView.as_view(), name="credit-gateway-test"),
    path("packages", CreditPackagesView.as_view(), name="credit-packages"),
    path("bundles", BundleListView.as_view(), name="credit-bundles"),
    path("bundles/purchase", BundlePurchaseView.as_view(), name="credit-bundle-purchase"),
    path("bundles/disable", BundleDisableView.as_view(), name="credit-bundle-disable"),
    path("entitlements", CompanyModuleEntitlementsView.as_view(), name="credit-entitlements"),
    path("usage-invoices", CompanyUsageInvoicesView.as_view(), name="credit-usage-invoices"),
    path("features", CreditFeaturesView.as_view(), name="credit-features"),
    path("logs", CreditLogsView.as_view(), name="credit-logs"),
    path("orders", CreateCreditOrderView.as_view(), name="credit-orders"),
    path("checkout-links", HostedCreditCheckoutView.as_view(), name="credit-hosted-links"),
    path("checkout-links/<uuid:purchase_id>/status", HostedCreditStatusView.as_view(), name="credit-hosted-status"),
    path("verify", VerifyCreditPaymentView.as_view(), name="credit-verify"),
    path("features/enable", EnableCreditFeatureView.as_view(), name="credit-feature-enable"),
    path("features/disable", DisableCreditFeatureView.as_view(), name="credit-feature-disable"),
    path("webhook", CreditWebhookView.as_view(), name="credit-razorpay-webhook"),
]
