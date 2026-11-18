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


def lock_running_features(company):
    from .models import CompanyPostpaidFeature
    return list(CompanyPostpaidFeature.objects.select_for_update().filter(
        company=company, enabled=True, suspended=False,
    ).order_by('pk'))


def reprice_staff_usage(company, features, cycle, at, *, tracked):
    from .cycle_billing import reconcile_cycle_label_seats
    from .monthly_features import meter_postpaid_feature
    for item in features:
        # Accrue using the stored rate, then snapshot the new seat count.
        meter_postpaid_feature(item, at=at, force=True, trusted_checkpoint=tracked)
    if cycle:
        reconcile_cycle_label_seats(company, at=at)


@contextmanager
def observe_staff_save(instance, fields, update):
    from apps.companies.models import Company
    from .cycle_billing import effective_policy
    from .monthly_features import _enabled

    timed = _enabled()
    cycle = effective_policy()
    if not (timed or cycle):
        yield
        return

    if update is not None:
        fields = tuple(field for field in fields if field in update or field.removesuffix('_id') in update)
    with transaction.atomic():
        records = type(instance).objects.filter(pk=instance.pk)
        previous_company_id = records.values_list('company_id', flat=True).first()
        company_ids = {previous_company_id, instance.company_id} - {None}
        companies = list(Company.objects.select_for_update().filter(pk__in=company_ids).order_by('pk'))
        old = records.values(*fields).first()
        if not companies or (old is not None and all(old[field] == getattr(instance, field) for field in fields)):
            yield
            return

        checkpoints = [(company, lock_running_features(company) if timed else ()) for company in companies]
        at = timezone.now()
        yield
        tracked = bool(enabled_policy())
        for company, features in checkpoints:
            reprice_staff_usage(company, features, cycle, at, tracked=tracked)


@contextmanager
def observe_save(instance, kwargs, *, storage=False):
    fields = ('size_bytes', 'file', 'company_id') if storage else (
        ('status', 'user_id', 'company_id') if instance._meta.model_name == 'employee'
        else ('is_active', 'is_staff', 'is_superuser', 'company_id'))
    update = kwargs.get('update_fields')
    if update is not None and not set(update).intersection(set(fields) | {'company', 'user'}):
        yield
        return
    if not storage:
        with observe_staff_save(instance, fields, update):
            yield
        return
    if not enabled_policy() or not instance.company_id:
        yield
        return
    with transaction.atomic():
        company = instance.company
        lock_company(company)
        old = type(instance).objects.filter(pk=instance.pk).values(*fields).first()
        if old and old['company_id'] != instance.company_id:
            raise ValueError('Move billable records by deleting/recreating them in the target company.')
        if instance.file and not instance.file._committed:
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
        from .runtime_billing import storage_state
        from .monthly_storage import meter_company_storage
        from apps.saas.storage_ledger import ensure_baseline
        state = storage_state(company)
        ensure_baseline(company)
        meter_company_storage(company, at=at, mutation=True)
        yield
        before = state.used_bytes
        state.used_bytes += max(0, instance.size_bytes) - max(0, old['size_bytes'] if old else 0)
        state.file_count += int(old is None)
        state.save()
        meter_company_storage(company, at=at, mutation=True)
        emit_storage_event(instance, 'REPLACE' if old else 'UPLOAD', before, state, at)


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
    if not instance.company_id:
        return
    if sender._meta.model_name != 'mediafile':
        from .cycle_billing import effective_policy
        from .monthly_features import _enabled
        timed = _enabled()
        cycle = effective_policy()
        if (timed or cycle) and lock_company(instance.company):
            features = lock_running_features(instance.company) if timed else ()
            instance._staff_billing_checkpoint = (features, cycle, timezone.now(), bool(enabled_policy()))
        return
    if not enabled_policy() or not lock_company(instance.company):
        return
    at = timezone.now()
    from .runtime_billing import storage_state
    from .monthly_storage import meter_company_storage
    from apps.saas.storage_ledger import ensure_baseline
    storage_state(instance.company)
    ensure_baseline(instance.company)
    meter_company_storage(instance.company, at=at, mutation=True)
    instance._billing_deleted_at = at


def after_delete(sender, instance, **kwargs):
    if sender._meta.model_name != 'mediafile':
        checkpoint = getattr(instance, '_staff_billing_checkpoint', None)
        if checkpoint:
            features, cycle, at, tracked = checkpoint
            from .models import CompanyPostpaidFeature
            # A company cascade may already have removed its meter rows.
            remaining = CompanyPostpaidFeature.objects.filter(pk__in=[item.pk for item in features])
            reprice_staff_usage(instance.company, remaining, cycle, at, tracked=tracked)
        return
    if not getattr(instance, '_billing_deleted_at', None):
        return
    at = timezone.now()
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
