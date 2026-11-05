from django.apps import AppConfig


class WhatsAppConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.whatsapp"

    def ready(self):
        from . import signals  # noqa: F401
