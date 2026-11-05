"""Issue a stable portal capability whenever a new customer is created."""
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Customer
from .portal_tokens import ensure_portal_access


@receiver(post_save, sender=Customer, dispatch_uid="cubegears.customer.portal-link")
def create_portal_link(sender, instance, created, raw=False, **kwargs):
    if created and not raw:
        ensure_portal_access(instance)
