from django.apps import AppConfig

class CreditsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.credits"

    def ready(self):
        from django.db.models.signals import pre_delete, post_delete
        from apps.saas.models import MediaFile
        from apps.accounts.models import User
        from apps.employees.models import Employee
        from .change_tracking import before_delete, after_delete
        for model in (MediaFile, User, Employee):
            pre_delete.connect(before_delete, sender=model, dispatch_uid=f"billing-before-{model._meta.label}")
            post_delete.connect(after_delete, sender=model, dispatch_uid=f"billing-after-{model._meta.label}")
