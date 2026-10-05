from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import *

router=DefaultRouter(trailing_slash=False)
router.register("billing/subscriptions",SubscriptionViewSet,basename="saas-subscriptions")
router.register("storage/history-records",StorageUsageViewSet,basename="storage-history-records")
router.register("templates",DocumentTemplateViewSet,basename="document-templates")
router.register("security/events",SecurityEventViewSet,basename="security-events")

urlpatterns=[
    path("billing",SubscriptionSummaryView.as_view(),name="saas-billing"),
    path("billing/usage",SubscriptionSummaryView.as_view(),name="saas-billing-usage"),
    path("storage",StorageSummaryView.as_view(),name="storage-summary"),
    path("storage/files",StorageFilesView.as_view(),name="storage-files"),
    path("storage/files/delete",StorageFilesDeleteView.as_view(),name="storage-files-delete"),
    path("storage/settings",StorageSettingsView.as_view(),name="storage-settings"),
    path("storage/history/<str:date>",StorageHistoryDateView.as_view(),name="storage-history-date"),
    path("settings",SettingsView.as_view(),name="settings-all"),
    path("settings/<str:category>",SettingsView.as_view(),name="settings-category"),
    *router.urls,
]
