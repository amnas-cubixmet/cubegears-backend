"""Read admin timing controls at execution time; no Beat restart after edits."""
from django.conf import settings
from django.utils import timezone


def policy():
    from .calendar_billing import active_policy
    return active_policy()


def checkpoint_meters(*, at=None):
    """Finish old rate snapshots, then install current rates for future time."""
    from apps.companies.models import Company
    from .models import CompanyPostpaidFeature
    from .monthly_storage import meter_company_storage
    from .monthly_features import meter_postpaid_feature
    at = at or timezone.now()
    if settings.STORAGE_MONTHLY_METERED_ENABLED:
        for company in Company.objects.filter(is_active=True).iterator():
            meter_company_storage(company, at=at, mutation=True)
    if settings.CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED:
        for item in CompanyPostpaidFeature.objects.filter(enabled=True, suspended=False).iterator():
            meter_postpaid_feature(item, at=at, force=True)


def storage_state(company):
    """One metadata sum on initialization; no file contents are read."""
    from django.db import transaction
    from django.db.models import Count, Sum
    from apps.companies.models import Company
    from apps.saas.models import MediaFile
    from .models import CompanyStorageState
    with transaction.atomic():
        Company.objects.select_for_update().get(pk=company.pk)
        state = CompanyStorageState.objects.filter(company=company).first()
        if state is None:
            totals = MediaFile.objects.filter(company=company).aggregate(bytes=Sum('size_bytes'), count=Count('pk'))
            state = CompanyStorageState.objects.create(
                company=company, used_bytes=max(0, totals['bytes'] or 0), file_count=totals['count'])
        return state
