"""Owner account metadata and read-only catalogue/audit records."""
from decimal import Decimal
from django.conf import settings
from rest_framework.response import Response
from rest_framework.views import APIView
from apps.accounts.permissions import IsCompanyOwner, MODEL_PERMISSION_PREFIX
from apps.saas.feature_discovery import module_inventory
from apps.saas.models import SecurityEvent
from .models import MonthlyUsageInvoice, FeatureMeterPeriod, StorageMeterPeriod


class CompanyAccountView(APIView):
    permission_classes = [IsCompanyOwner]

    def get(self, request):
        company = request.user.company
        domain = getattr(settings, 'TENANT_BASE_DOMAIN', '').strip('.')
        return Response({
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
        result = []
        for _, item in sorted(months.items(), reverse=True):
            statuses = item.pop('statuses')
            item['status'] = 'paid' if all(s == 'paid' for s in statuses) else 'insufficient' if 'insufficient' in statuses else 'pending'
            item['totalCredits'] = str(item['featureCredits'] + item['storageCredits'])
            item['featureCredits'] = str(item['featureCredits'])
            item['storageCredits'] = str(item['storageCredits'])
            result.append(item)
        return Response(result)
