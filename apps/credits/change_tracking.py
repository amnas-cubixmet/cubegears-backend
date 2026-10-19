"""Transactional mutation checkpoints for application-owned files and staff.

Bulk update/create and out-of-band filesystem writes must use explicit
reconciliation; Django intentionally does not call model.save for those paths.
"""
from contextlib import contextmanager
from django.conf import settings
from django.db import transaction
from django.utils import timezone


def enabled_policy():
    if not (settings.STORAGE_MONTHLY_METERED_ENABLED or settings.CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED):
        return None
    from .runtime_billing import policy
    current = policy()
    return current if current and current.event_tracking_enabled else None


def lock_company(company):
    from apps.companies.models import Company
    return Company.objects.select_for_update().filter(pk=company.pk).exists()


def checkpoint_features(company, at):
    from .models import CompanyPostpaidFeature
    from .monthly_features import meter_postpaid_feature
    for item in CompanyPostpaidFeature.objects.filter(company=company, enabled=True, suspended=False).order_by('pk'):
        meter_postpaid_feature(item, at=at, force=True)


@contextmanager
def observe_save(instance, kwargs, *, storage=False):
    if not enabled_policy() or not instance.company_id:
        yield
        return
    fields = ('size_bytes', 'file', 'company_id') if storage else (
        ('status', 'user_id', 'company_id') if instance._meta.model_name == 'employee'
        else ('is_active', 'is_staff', 'is_superuser', 'company_id'))
    update = kwargs.get('update_fields')
    if update is not None and not set(update).intersection(set(fields) | {'company', 'user'}):
        yield
        return
    with transaction.atomic():
        company = instance.company
        lock_company(company)
        old = type(instance).objects.filter(pk=instance.pk).values(*fields).first()
        if old and old['company_id'] != instance.company_id:
            raise ValueError('Move billable records by deleting/recreating them in the target company.')
        if storage and instance.file and not instance.file._committed:
            instance.size_bytes = instance.file.size
            if update is not None:
                kwargs['update_fields'] = set(update) | {'size_bytes'}
        changed = old is None or any(
            old[name] != (instance.file.name if name == 'file' else getattr(instance, name))
            for name in fields)
        if not changed:
            yield
            return
        at = timezone.now()
        if storage:
            from .runtime_billing import storage_state
            from .monthly_storage import meter_company_storage
            from apps.saas.storage_ledger import ensure_baseline
            state = storage_state(company)
            ensure_baseline(company)
            meter_company_storage(company, at=at, mutation=True)
        else:
            checkpoint_features(company, at)
        yield
        if storage:
            before = state.used_bytes
            state.used_bytes += max(0, instance.size_bytes) - max(0, old['size_bytes'] if old else 0)
            state.file_count += int(old is None)
            state.save()
            meter_company_storage(company, at=at, mutation=True)
            emit_storage_event(instance, 'REPLACE' if old else 'UPLOAD', before, state, at)
        else:
            checkpoint_features(company, at)


def emit_storage_event(instance, kind, before, state, at):
    from apps.saas.models import StorageEvent
    StorageEvent.objects.create(
        company=instance.company, branch=instance.branch, event_type=kind,
        file_id_snapshot=instance.pk, name_snapshot=instance.name,
        category_snapshot=instance.category, mime_type_snapshot=instance.mime_type,
        file_size_bytes=instance.size_bytes, delta_bytes=state.used_bytes-before,
        storage_before_bytes=before, storage_after_bytes=state.used_bytes,
        file_count_after=state.file_count,
        actor=getattr(instance, '_storage_actor', instance.uploaded_by if kind == 'UPLOAD' else None),
        occurred_at=at)


def before_delete(sender, instance, **kwargs):
    if not enabled_policy() or not instance.company_id:
        return
    if not lock_company(instance.company):
        return
    at = timezone.now()
    if sender._meta.model_name == 'mediafile':
        from .runtime_billing import storage_state
        from .monthly_storage import meter_company_storage
        from apps.saas.storage_ledger import ensure_baseline
        storage_state(instance.company)
        ensure_baseline(instance.company)
        meter_company_storage(instance.company, at=at, mutation=True)
    else:
        checkpoint_features(instance.company, at)
    instance._billing_deleted_at = at


def after_delete(sender, instance, **kwargs):
    at = getattr(instance, '_billing_deleted_at', None)
    if at is None:
        return
    at = timezone.now()
    if sender._meta.model_name == 'mediafile':
        from .models import CompanyStorageState
        from .monthly_storage import meter_company_storage
        state = CompanyStorageState.objects.select_for_update().filter(company_id=instance.company_id).first()
        if state is None:  # Company cascade deleted the counter too.
            return
        before = state.used_bytes
        state.used_bytes = max(0, state.used_bytes - max(0, instance.size_bytes))
        state.file_count = max(0, state.file_count - 1)
        state.save()
        meter_company_storage(instance.company, at=at, mutation=True)
        emit_storage_event(instance, 'DELETE', before, state, at)
    else:
        checkpoint_features(instance.company, at)
