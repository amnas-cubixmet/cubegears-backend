"""Bundle entitlement lookup. Pure read-only code."""
from django.db.models import Q
from django.utils import timezone
from .models import CompanyFeatureBundle

def active_bundle_access(company, module):
    return CompanyFeatureBundle.objects.filter(
        company=company,
        enabled=True,
        bundle__is_active=True,
        bundle__features__key=module,
        bundle__features__is_active=True,
    ).filter(
        Q(bundle__period="permanent")
        | Q(bundle__period="per_use", remaining_uses__gt=0)
        | Q(bundle__period__in=["hourly", "daily", "weekly"], expires_at__gt=timezone.now())
    ).exists()
