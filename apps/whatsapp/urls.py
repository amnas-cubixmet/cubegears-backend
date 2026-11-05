from django.urls import path
from .views import (
    WhatsAppSettingsView, WhatsAppConnectView, WhatsAppDisconnectView,
    WhatsAppTemplateView, WhatsAppLogsView, WhatsAppCustomerPreferenceView,
    WhatsAppTestMessageView,
)
from .webhook import MetaWhatsAppWebhookView
from .manual import WhatsAppManualShareView

urlpatterns = [
    path("settings", WhatsAppSettingsView.as_view(), name="whatsapp-settings"),
    path("connect", WhatsAppConnectView.as_view(), name="whatsapp-connect"),
    path("disconnect", WhatsAppDisconnectView.as_view(), name="whatsapp-disconnect"),
    path("templates", WhatsAppTemplateView.as_view(), name="whatsapp-templates"),
    path("logs", WhatsAppLogsView.as_view(), name="whatsapp-logs"),
    path("customers/<uuid:customer_id>/consent", WhatsAppCustomerPreferenceView.as_view(), name="whatsapp-consent"),
    path("test", WhatsAppTestMessageView.as_view(), name="whatsapp-test"),
    path("manual-share", WhatsAppManualShareView.as_view(), name="whatsapp-manual-share"),
    path("webhook", MetaWhatsAppWebhookView.as_view(), name="whatsapp-webhook"),
]
