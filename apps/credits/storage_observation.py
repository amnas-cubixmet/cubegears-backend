"""Observe storage before and after mutations; deletes never erase accrued periods."""
from django.conf import settings
from .models import StorageCreditPolicy


def observe_storage(company, at):
    if settings.STORAGE_MONTHLY_METERED_ENABLED:
        from .monthly_storage import meter_company_storage
        meter_company_storage(company, at=at, mutation=True)
    else:
        policy = StorageCreditPolicy.objects.filter(is_active=True, billing_mode='metered_monthly').first()
        if policy:
            from .postpaid import record_storage_interval
            record_storage_interval(company, now=at, policy=policy, mutation=True)
