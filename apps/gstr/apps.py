from django.apps import AppConfig


class GstrConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.gstr"

    def ready(self):
        from . import signals  # noqa: F401; register synchronous tax reversals
