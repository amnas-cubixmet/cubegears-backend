"""Dedicated Celery storage jobs (only scheduled when explicitly opted in)."""
from celery import shared_task
from .monthly_storage import meter_all_company_storage, settle_due_storage


@shared_task(name="cubegear.storage.sample")
def sample_company_storage():
    return meter_all_company_storage()


@shared_task(name="cubegear.storage.settle")
def settle_closed_storage_months():
    return settle_due_storage()
