from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import *
from .payment_views import (PaymentOrderView, PaymentVerifyView, PaymentHistoryView, PaymentAccessView, RazorpayWebhookView)
from .plan_views import PlanCatalogView, PlanAccessView, PlanChangeRequestView, CustomPlanRequestView

router=DefaultRouter(trailing_slash=False)
router.register("billing/subscriptions",SubscriptionViewSet,basename="saas-subscriptions")
router.register("storage/history-records",StorageUsageViewSet,basename="storage-history-records")
router.register("templates",DocumentTemplateViewSet,basename="document-templates")
router.register("security/events",SecurityEventViewSet,basename="security-events")

urlpatterns=[
    path('checkout/status', CheckoutStatusView.as_view(), name='saas-checkout-status'),
    path('checkout/orders', CheckoutCreateView.as_view(), name='saas-checkout-orders'),
    path('checkout/verify', CheckoutVerifyView.as_view(), name='saas-checkout-verify'),
    path('checkout/history', CheckoutPaymentHistoryView.as_view(), name='saas-checkout-history'),
    path('checkout/orders/<str:order_id>', CheckoutOrderStatusView.as_view(), name='saas-checkout-order-status'),
    path('checkout/webhook', RazorpayWebhookView.as_view(), name='saas-checkout-webhook'),
    path("plans", PlanCatalogView.as_view(), name="saas-plan-catalog"),
    path("payments/access", PaymentAccessView.as_view(), name="saas-payment-access"),
    path("payments/orders", PaymentOrderView.as_view(), name="saas-payment-orders"),
    path("payments/verify", PaymentVerifyView.as_view(), name="saas-payment-verify"),
    path("payments/history", PaymentHistoryView.as_view(), name="saas-payment-history"),
    path("payments/webhook", RazorpayWebhookView.as_view(), name="saas-payment-webhook"),
    path("plan-access", PlanAccessView.as_view(), name="saas-plan-access"),
    path("billing/change-requests", PlanChangeRequestView.as_view(), name="saas-plan-changes"),
    path("billing/custom-requests", CustomPlanRequestView.as_view(), name="saas-custom-plan-requests"),
    path("billing",SubscriptionSummaryView.as_view(),name="saas-billing"),
    path("billing/usage",SubscriptionSummaryView.as_view(),name="saas-billing-usage"),
    path("storage",StorageSummaryView.as_view(),name="storage-summary"),
    path("storage/files",StorageFilesView.as_view(),name="storage-files"),
    path("storage/files/delete",StorageFilesDeleteView.as_view(),name="storage-files-delete"),
    path("storage/files/<uuid:file_id>/download",StorageFileDownloadView.as_view(),name="storage-file-download"),
    path("storage/settings",StorageSettingsView.as_view(),name="storage-settings"),
    path("storage/history",StorageHistoryListView.as_view(),name="storage-history-list"),
    path("storage/events",StorageEventsView.as_view(),name="storage-events"),
    path("storage/history/<str:date>",StorageHistoryDateView.as_view(),name="storage-history-date"),
    path("settings",SettingsView.as_view(),name="settings-all"),
    path("settings/<str:category>",SettingsView.as_view(),name="settings-category"),
    *router.urls,
]
