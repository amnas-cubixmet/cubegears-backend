from django.apps import AppConfig


class WhatsAppConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.whatsapp"
    # Historical models stay installed for migrations and audit records.
    # No automatic job/invoice messaging signal receivers are registered.
