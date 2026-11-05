"""Observe existing Job and Invoice changes without changing their workflows."""
from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from apps.jobs.models import Job
from apps.invoices.models import Invoice
from .tasks import queue_customer_notification


@receiver(pre_save, sender=Job, dispatch_uid="cubegears.whatsapp.job.previous")
def remember_job_state(sender, instance, **kwargs):
    previous = Job.objects.filter(pk=instance.pk).values("status", "inspection").first() if instance.pk else None
    instance._whatsapp_previous = previous


@receiver(post_save, sender=Job, dispatch_uid="cubegears.whatsapp.job.changed")
def on_job_changed(sender, instance, created, **kwargs):
    previous = getattr(instance, "_whatsapp_previous", None) or {}
    events = []
    if created:
        events.append("job_created")
    elif instance.status != previous.get("status"):
        events.append("status_changed")
        if instance.status in {"Approved", "In Progress"}:
            events.append("work_started")
        if instance.status == "Ready for Delivery":
            events.append("ready_delivery")
        if instance.status in {"QC", "Delivered"}:
            events.append("work_completed")
    completed = (instance.inspection or {}).get("status") == "Completed"
    old_completed = (previous.get("inspection") or {}).get("status") == "Completed"
    if completed and not old_completed:
        events.append("inspection_completed")
    if not events:
        return
    job_id, company_id, customer_id = instance.pk, instance.company_id, instance.customer_id
    def schedule():
        job = Job.objects.select_related("customer", "vehicle").filter(
            pk=job_id, company_id=company_id, customer_id=customer_id,
        ).first()
        if not job:
            return
        for event in events:
            queue_customer_notification(
                job.customer, event, f"{job.pk}:{event}:{job.status}", job=job,
            )
    transaction.on_commit(schedule)


@receiver(pre_save, sender=Invoice, dispatch_uid="cubegears.whatsapp.invoice.previous")
def remember_invoice_state(sender, instance, **kwargs):
    instance._wa_previous_finalized = Invoice.objects.filter(pk=instance.pk).values_list(
        "finalized_at", flat=True,
    ).first() if instance.pk else None


@receiver(post_save, sender=Invoice, dispatch_uid="cubegears.whatsapp.invoice.published")
def on_invoice_published(sender, instance, **kwargs):
    if not instance.finalized_at or instance.status in {"Draft", "Cancelled"}:
        return
    if getattr(instance, "_wa_previous_finalized", None):
        return
    invoice_id, company_id = instance.pk, instance.company_id
    def schedule():
        inv = Invoice.objects.select_related("customer", "job__vehicle").filter(
            pk=invoice_id, company_id=company_id, finalized_at__isnull=False,
        ).first()
        if inv:
            queue_customer_notification(
                inv.customer, "invoice_published", f"invoice:{inv.pk}:published", job=inv.job,
            )
    transaction.on_commit(schedule)
