from django.urls import path
from .views import (
    CompanyCreditsView, CreditPackagesView, CreditFeaturesView,
    CreditLogsView, CreateCreditOrderView, VerifyCreditPaymentView,
    EnableCreditFeatureView, DisableCreditFeatureView, CreditWebhookView,
)

urlpatterns = [
    path("overview", CompanyCreditsView.as_view(), name="credit-overview"),
    path("packages", CreditPackagesView.as_view(), name="credit-packages"),
    path("features", CreditFeaturesView.as_view(), name="credit-features"),
    path("logs", CreditLogsView.as_view(), name="credit-logs"),
    path("orders", CreateCreditOrderView.as_view(), name="credit-orders"),
    path("verify", VerifyCreditPaymentView.as_view(), name="credit-verify"),
    path("features/enable", EnableCreditFeatureView.as_view(), name="credit-feature-enable"),
    path("features/disable", DisableCreditFeatureView.as_view(), name="credit-feature-disable"),
    path("webhook", CreditWebhookView.as_view(), name="credit-razorpay-webhook"),
]
