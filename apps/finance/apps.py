from django.apps import AppConfig

class FinanceConfig(AppConfig):
    name = "apps.finance"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from . import signals  # noqa: F401: register the accounting source adapters
