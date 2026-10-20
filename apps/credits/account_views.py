"""Owner account metadata and read-only catalogue/audit records."""
from decimal import Decimal
from django.conf import settings
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView
from apps.accounts.permissions import IsCompanyOwner, MODEL_PERMISSION_PREFIX
from apps.saas.feature_discovery import module_inventory
from apps.saas.models import SecurityEvent
from .models import MonthlyUsageInvoice, FeatureMeterPeriod, StorageMeterPeriod


class CompanyAccountView(APIView):
    permission_classes = [IsCompanyOwner]

    def get(self, request):
        from .monthly_policy import billing_status
        company = request.user.company
        domain = getattr(settings, 'TENANT_BASE_DOMAIN', '').strip('.')
        return Response({
            'billingStatus': billing_status(company),
            'companyId': str(company.pk), 'companyName': company.name,
            'subdomain': company.slug,
            'accountPath': '/account',
            # DNS and wildcard TLS must be provisioned by the hosting operator.
            'configuredTenantUrl': f'https://{company.slug}.{domain}/account' if domain else None,
            'modules': [{**row, 'models': sorted(
                name for name, prefix in MODEL_PERMISSION_PREFIX.items() if prefix == row['key']
            )} for row in module_inventory()],
        })


class FeatureActivityView(APIView):
    permission_classes = [IsCompanyOwner]

    def get(self, request):
        rows = SecurityEvent.objects.filter(
            company=request.user.company, event__startswith='feature_',
        ).select_related('user')[:100]
        return Response([{
            'id': str(row.pk), 'event': row.event, 'createdAt': row.created_at.isoformat(),
            'actor': row.user.name if row.user_id else None, 'details': row.metadata,
        } for row in rows])


class MonthlyStatementsView(APIView):
    permission_classes = [IsCompanyOwner]

    def get(self, request):
        # These are persisted settlement invoices, not browser estimates.
        rows = MonthlyUsageInvoice.objects.filter(company=request.user.company).order_by('-billing_month', 'category')[:48]
        months = {}
        for row in rows:
            if row.category == 'feature' and settings.CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED:
                continue
            if row.category == 'storage' and settings.STORAGE_MONTHLY_METERED_ENABLED:
                continue
            item = months.setdefault(row.billing_month, {
                'id': row.billing_month, 'period': row.billing_month,
                'featureCredits': Decimal('0'), 'storageCredits': Decimal('0'), 'statuses': [],
            })
            item['featureCredits' if row.category == 'feature' else 'storageCredits'] += row.credits_due
            item['statuses'].append(row.status)
        # The newer metered engine persists periods rather than legacy invoices.
        # Each enabled engine supplies its own category, avoiding double counting.
        categories = []
        if settings.CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED:
            categories.append(('featureCredits', FeatureMeterPeriod.objects.filter(company=request.user.company)))
        if settings.STORAGE_MONTHLY_METERED_ENABLED:
            categories.append(('storageCredits', StorageMeterPeriod.objects.filter(company=request.user.company)))
        for category, periods in categories:
            per_month = {}
            for row in periods.order_by('-period')[:240]:
                value = row.accrued_credits + (row.activation_credits if category == 'featureCredits' else 0)
                part = per_month.setdefault(row.period, {'credits': Decimal('0'), 'statuses': []})
                part['credits'] += value
                part['statuses'].append('paid' if row.settled_at else 'pending')
            for period, part in per_month.items():
                item = months.setdefault(period, {
                    'id': period, 'period': period, 'featureCredits': Decimal('0'),
                    'storageCredits': Decimal('0'), 'statuses': [],
                })
                item[category] = part['credits'].quantize(Decimal('0.01'))
                item['statuses'].extend(part['statuses'])
        # The calendar statement, once issued, is the only authoritative
        # invoice. Meter rows for an open month are provisional estimates.
        from .calendar_billing import active_policy, deadline_for
        from .models import BillingCycleStatement
        active_calendar = active_policy()
        issued = {
            row.month: row for row in BillingCycleStatement.objects.filter(
                company=request.user.company,
            ).order_by("-month")[:48]
        } if active_calendar else {}
        for month, row in issued.items():
            months.setdefault(month, {
                'id': month, 'period': month, 'featureCredits': Decimal('0'),
                'storageCredits': Decimal('0'), 'statuses': [],
            })
        result = []
        for _, item in sorted(months.items(), reverse=True):
            statuses = item.pop('statuses')
            statement = issued.get(item['period'])
            if statement:
                item['featureCredits'] = statement.feature_credits
                item['storageCredits'] = statement.storage_credits
                item['status'] = statement.status
                item['dueAt'] = statement.due_at.isoformat()
                item['overdue'] = bool(statement.status != 'paid' and
                    timezone.localdate() > timezone.localtime(statement.due_at).date())
            else:
                item['status'] = 'paid' if statuses and all(s == 'paid' for s in statuses) else 'insufficient' if 'insufficient' in statuses else 'pending'
                if active_calendar:
                    item['dueAt'] = deadline_for(
                        item['period'], active_calendar.due_day, active_calendar.due_time,
                    ).isoformat()
                    item['overdue'] = False  # Not an issued invoice yet.
                else:
                    from .monthly_policy import deadline, overdue
                    cycle = deadline(item['period'])
                    item['dueAt'] = cycle.due_at.isoformat() if cycle else None
                    item['overdue'] = bool(cycle and item['status'] != 'paid' and overdue(cycle, timezone.now()))
            item['totalCredits'] = str(item['featureCredits'] + item['storageCredits'])
            item['featureCredits'] = str(item['featureCredits'])
            item['storageCredits'] = str(item['storageCredits'])
            result.append(item)
        return Response(result)


class FeatureUsageLogView(APIView):
    """Read-only, company-scoped module usage and rate snapshots for owners."""
    permission_classes = [IsCompanyOwner]

    def get(self, request):
        from .models import CompanyPostpaidFeature, FeatureMeterReading, FeatureMeterPeriod
        from .monthly_storage import _period_for
        from .period_rates import PERIOD_LABELS
        from .calendar_billing import active_policy

        company = request.user.company
        if not company:
            return Response({"detail": "Company required."}, status=403)
        current = _period_for(timezone.now())
        states = CompanyPostpaidFeature.objects.filter(
            company=company,
        ).select_related("feature").order_by("feature__label")
        readings = FeatureMeterReading.objects.filter(
            company=company,
        ).select_related("feature").order_by("-sampled_at", "-pk")[:120]
        periods = FeatureMeterPeriod.objects.filter(
            company=company,
        ).select_related("feature").order_by("-period", "feature__label")[:120]
        active_policy_record = active_policy()
        current_credits = sum(
            (row.accrued_credits + row.activation_credits for row in periods if row.period == current),
            Decimal("0"),
        )
        return Response({
            "currentMonth": current,
            "currentMonthLoggedCredits": str(current_credits.quantize(Decimal("0.01"))),
            "dueDay": active_policy_record.due_day if active_policy_record else None,
            "activeModules": [{
                "feature": state.feature.key,
                "name": state.feature.label,
                "enabled": state.enabled and not state.suspended,
                "suspended": state.suspended,
                "rateCredits": str(state.monthly_rate),
                "billingPeriod": state.billing_period,
                "periodLabel": PERIOD_LABELS.get(state.billing_period, state.billing_period),
                "rateDurationSeconds": state.rate_duration_seconds,
                "lastMeteredAt": state.sampled_at.isoformat() if state.sampled_at else None,
            } for state in states],
            "readings": [{
                "id": row.pk,
                "feature": row.feature.key,
                "name": row.feature.label,
                "sampledAt": row.sampled_at.isoformat(),
                "userCount": row.users,
                "staffCount": row.staff,
                "coveredSeconds": row.covered_seconds,
                "rateCredits": str(row.monthly_rate),
                "billingPeriod": row.billing_period,
                "periodLabel": PERIOD_LABELS.get(row.billing_period, row.billing_period),
                "rateDurationSeconds": row.rate_duration_seconds,
                "accruedCredits": str(row.accrued_credits),
            } for row in readings],
            "monthlyTotals": [{
                "feature": row.feature.key,
                "name": row.feature.label,
                "month": row.period,
                "coveredSeconds": row.covered_seconds,
                "accruedCredits": str(row.accrued_credits + row.activation_credits),
                "settled": row.settled_at is not None,
            } for row in periods],
        })
