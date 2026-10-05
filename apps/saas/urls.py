from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import *
router=DefaultRouter(trailing_slash=False)
router.register("billing",SubscriptionViewSet,basename="saas-billing")
router.register("storage/history",StorageUsageViewSet,basename="storage-history")
router.register("templates",DocumentTemplateViewSet,basename="document-templates")
router.register("security/events",SecurityEventViewSet,basename="security-events")
urlpatterns=[
 path("storage",StorageSummaryView.as_view(),name="storage-summary"),
 path("settings",SettingsView.as_view(),name="settings-all"),
 path("settings/<str:category>",SettingsView.as_view(),name="settings-category"),
 *router.urls
]
