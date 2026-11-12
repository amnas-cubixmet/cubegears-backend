"""Manual-only WhatsApp sharing endpoints. No provider or webhook routes."""
from django.urls import path
from .views import WhatsAppSettingsView, WhatsAppLogsView, WhatsAppCustomerPreferenceView
from .manual import WhatsAppManualShareView

urlpatterns = [
    path("settings", WhatsAppSettingsView.as_view(), name="whatsapp-settings"),
    path("logs", WhatsAppLogsView.as_view(), name="whatsapp-logs"),
    path("customers/<uuid:customer_id>/consent", WhatsAppCustomerPreferenceView.as_view(), name="whatsapp-consent"),
    path("manual-share", WhatsAppManualShareView.as_view(), name="whatsapp-manual-share"),
]
