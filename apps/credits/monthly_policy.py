"""Shared collection calendar and read-only debt status for existing billing engines."""
from contextvars import ContextVar
import calendar
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from .models import (MonthlyBillingPolicy, MonthlyBillingCycle, MonthlyUsageInvoice,
                     FeatureMeterPeriod, StorageMeterPeriod, StorageCreditPolicy, CreditLedger)


request_billing_cache = ContextVar('monthly_billing_cache', default=None)

def policy():
    return MonthlyBillingPolicy.objects.filter(pk=1, enabled=True).first()


def deadline(period, *, config=None, issue=False):
    config = config or policy()
    if not config:
        return None
    frozen = MonthlyBillingCycle.objects.filter(period=period).first()
    if frozen:
        return frozen
    year, month = map(int, period.split('-'))
    year, month = year + (month == 12), 1 if month == 12 else month + 1
    day = min(config.due_day, calendar.monthrange(year, month)[1])
    due = timezone.make_aware(datetime(year, month, day), timezone.get_default_timezone())
    if issue:
        return MonthlyBillingCycle.objects.get_or_create(period=period, defaults={
            'due_at': due, 'lock_after_due': config.lock_after_due,
        })[0]
    return MonthlyBillingCycle(period=period, due_at=due, lock_after_due=config.lock_after_due)


def collection_waiting(period, at, *, dry_run=False):
    cycle = deadline(period, issue=not dry_run)
    return bool(cycle and at < cycle.due_at)


def overdue(cycle, at):
    # Collection starts on the due day. Access lasts THROUGH that local day.
    next_day = timezone.localdate(cycle.due_at) + timedelta(days=1)
    cutoff = timezone.make_aware(datetime.combine(next_day, datetime.min.time()), timezone.get_default_timezone())
    return at >= cutoff


def legacy_periods(company, category, at=None):
    from .models import FeatureUsageSession, StorageUsageInterval
    if category == 'storage':
        return set(StorageUsageInterval.objects.filter(company=company).values_list('billing_month', flat=True))
    first = FeatureUsageSession.objects.filter(company=company).order_by('started_at').values_list('started_at', flat=True).first()
    if first is None:
        return set()
    current = timezone.localtime(at or timezone.now(), timezone.get_default_timezone()).strftime('%Y-%m')
    period = timezone.localtime(first, timezone.get_default_timezone()).strftime('%Y-%m')
    periods = set()
    while period < current:
        periods.add(period)
        year, month = map(int, period.split('-'))
        period = f'{year + (month == 12):04d}-{1 if month == 12 else month + 1:02d}'
    return periods


def legacy_debts(company, category, at=None):
    from .postpaid import feature_month_quote, storage_month_quote
    invoices = {row.billing_month: row for row in MonthlyUsageInvoice.objects.filter(company=company, category=category)}
    for period in legacy_periods(company, category, at) | set(invoices):
        if period in invoices:
            if invoices[period].status != 'paid':
                yield period, invoices[period].credits_due, category
        else:
            amount, _ = (feature_month_quote if category == 'feature' else storage_month_quote)(company, period)
            yield period, amount, category


def unpaid_rows(company, at=None):
    """Read only the selected engine per category, never duplicate old/new charges."""
    if settings.CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED:
        for row in FeatureMeterPeriod.objects.filter(company=company, settled_at__isnull=True).select_related('feature'):
            from apps.saas.access import FREE_TENANT_MODULES
            if row.feature.key not in FREE_TENANT_MODULES:
                yield row.period, row.accrued_credits + row.activation_credits, 'feature'
    elif settings.CREDIT_POSTPAID_FEATURE_BILLING_ENABLED:
        if not getattr(settings, 'CREDIT_LABEL_ONLY_ACCESS_ENABLED', False):
            yield from legacy_debts(company, 'feature', at)
    if settings.STORAGE_MONTHLY_METERED_ENABLED:
        for row in StorageMeterPeriod.objects.filter(company=company, settled_at__isnull=True):
            if CreditLedger.objects.filter(company=company, reference=f'storage:{company.pk}:monthly:{row.period}').exists():
                continue
            yield row.period, row.accrued_credits, 'storage'
    elif StorageCreditPolicy.objects.filter(is_active=True, billing_mode='metered_monthly').exists():
        yield from legacy_debts(company, 'storage', at)


def _billing_status(company, *, at=None):
    at = at or timezone.now()
    config = policy()
    result = {'enabled': bool(config), 'dueDay': config.due_day if config else None,
              'blocked': False, 'outstandingCredits': '0.00', 'overdueCredits': '0.00', 'periods': []}
    if not config:
        return result
    current = timezone.localtime(at, timezone.get_default_timezone()).strftime('%Y-%m')
    periods = {}
    for period, amount, category in unpaid_rows(company, at):
        if period >= current or amount.quantize(Decimal('.01')) <= 0:
            continue
        cycle = deadline(period, config=config)
        item = periods.setdefault(period, {'period': period, 'credits': Decimal('0'),
            'dueAt': cycle.due_at.isoformat(), 'overdue': overdue(cycle, at),
            'blocksAccess': cycle.lock_after_due and overdue(cycle, at)})
        item['credits'] += amount
    outstanding = late = Decimal('0')
    for item in sorted(periods.values(), key=lambda x: x['period']):
        amount = item['credits'].quantize(Decimal('.01'))
        outstanding += amount
        if item['overdue']:
            late += amount
        result['blocked'] |= item['blocksAccess']
        item['credits'] = str(amount)
        result['periods'].append(item)
    result.update(outstandingCredits=str(outstanding.quantize(Decimal('.01'))), overdueCredits=str(late.quantize(Decimal('.01'))))
    return result


def billing_status(company, *, at=None):
    cache = request_billing_cache.get()
    if cache is None or at is not None:
        return _billing_status(company, at=at)
    if company.pk not in cache:
        cache[company.pk] = _billing_status(company)
    return cache[company.pk]


def retry_company_bills(company, *, at=None):
    """Use existing idempotent settlement functions, including older unpaid months."""
    cache = request_billing_cache.get()
    if cache is not None:
        cache.pop(company.pk, None)
    if not policy():
        return
    at = at or timezone.now()
    current = timezone.localtime(at, timezone.get_default_timezone()).strftime('%Y-%m')
    if settings.CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED:
        from .monthly_features import settle_postpaid_feature
        for row in FeatureMeterPeriod.objects.filter(company=company, settled_at__isnull=True, period__lt=current).select_related('feature').order_by('period', 'id'):
            settle_postpaid_feature(company, row.feature, row.period, at=at)
    if settings.STORAGE_MONTHLY_METERED_ENABLED:
        from .monthly_storage import settle_company_storage
        for period in StorageMeterPeriod.objects.filter(company=company, settled_at__isnull=True, period__lt=current).order_by('period').values_list('period', flat=True):
            settle_company_storage(company, period, at=at)
    from .postpaid import settle_monthly_usage, completed_month
    for category in ('feature', 'storage'):
        if (category == 'feature' and (not settings.CREDIT_POSTPAID_FEATURE_BILLING_ENABLED or settings.CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED)) or (category == 'storage' and settings.STORAGE_MONTHLY_METERED_ENABLED):
            continue
        periods = set(MonthlyUsageInvoice.objects.filter(company=company, category=category, billing_month__lt=current).exclude(status='paid').values_list('billing_month', flat=True))
        periods |= legacy_periods(company, category, at)
        periods.add(completed_month(at))
        for period in sorted(periods):
            settle_monthly_usage(company, category, period, now=at)


    if not billing_status(company, at=at)['blocked']:
        from django.db.models import Q
        from .models import CompanyPostpaidFeature
        CompanyPostpaidFeature.objects.filter(
            company=company, enabled=True, suspended=True,
            feature__is_active=True, feature__credit_price__is_available=True,
        ).exclude(Q(feature__module_price_label__monthly_postpaid=True) & Q(feature__module_price_label__bundle__is_active=False)).update(
            suspended=False, sampled_at=at,
        )


def retry_after_topup(company):
    # A failed billing retry must never roll back a verified captured payment.
    try:
        with transaction.atomic():
            retry_company_bills(company)
    except Exception:
        logging.getLogger(__name__).exception('Monthly collection retry failed for company=%s', company.pk)
    finally:
        cache = request_billing_cache.get()
        if cache is not None:
            cache.pop(company.pk, None)
