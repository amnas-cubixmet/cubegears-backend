"""A posted purchase return reverses only previously approved GST credit."""
from django.db.models.signals import post_save
from django.dispatch import receiver
from apps.inventory.models import SupplierDebitNote
from .itc import reverse_credit_for_return


@receiver(post_save, sender=SupplierDebitNote, dispatch_uid="gstr_part15_itc_return_reversal")
def record_itc_return(sender, instance, created=False, raw=False, **kwargs):
    if created and not raw:
        reverse_credit_for_return(instance)
