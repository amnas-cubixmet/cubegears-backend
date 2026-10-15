"""Plan entitlement checks. Legacy subscriptions stay compatible until linked to a plan."""
from rest_framework.exceptions import ValidationError
from .models import Subscription


def active_plan(company):
    if not company:
        return None
    sub = (
        Subscription.objects.filter(company=company, status__iexact="Active")
        .select_related("plan_definition").order_by("-created_at").first()
    )
    if sub and sub.plan_definition_id and sub.plan_definition.is_active:
        return sub.plan_definition
    return None


def enforce_branch_limit(company):
    plan = active_plan(company)
    if not plan:
        return
    from apps.branches.models import Branch
    if Branch.objects.filter(company=company, is_active=True).count() >= plan.max_branches:
        raise ValidationError({"branch": f"Your {plan.name} plan allows {plan.max_branches} active branches. Request an upgrade."})


def enforce_user_limit(company):
    plan = active_plan(company)
    if not plan:
        return
    from apps.accounts.models import User
    if User.objects.filter(company=company, is_active=True).count() >= plan.max_users:
        raise ValidationError({"users": f"Your {plan.name} plan allows {plan.max_users} active users. Request an upgrade."})



def active_label_modules(company):
    """Only currently usable, company-purchased label modules count.

    No role permissions are granted here. Individual paid features and
    standalone price rows do NOT qualify for label-only access.
    """
    if not company:
        return set()
    from django.db.models import Q
    from django.utils import timezone
    from apps.credits.models import CompanyBundleAccess
    now = timezone.now()
    return set(CompanyBundleAccess.objects.filter(
        company=company,
        enabled=True,
        bundle__is_active=True,
        bundle__features__is_active=True,
    ).filter(
        Q(billing_type_at_purchase="permanent", valid_until__isnull=True)
        | Q(billing_type_at_purchase__in=("hourly", "daily", "weekly", "monthly"),
            valid_until__gt=now)
    ).values_list("bundle__features__key", flat=True).distinct())



def feature_allowed(user, permission):
    """Paid feature access never grants tenant RBAC; it can only restrict it."""
    from django.conf import settings
    module = permission.split(".")[0]
    # Core pages remain available so a company can purchase/recover access.
    if module in {"dashboard", "billing", "settings", "company", "storage",
                  "notifications", "roles"}:
        return True

    # Strict label-only mode is OPT-IN. Fail closed if it is enabled without
    # the paid-access enforcement switch; never treat a missing config as a grant.
    if getattr(settings, "CREDIT_LABEL_ONLY_ACCESS_ENABLED", False):
        return bool(settings.CREDIT_FEATURE_ENFORCEMENT_ENABLED
                    and module in active_label_modules(user.company))

    if not settings.CREDIT_FEATURE_ENFORCEMENT_ENABLED:
        return True
    if permission == "attendance.self" and not settings.CREDIT_STRICT_MODULE_GATING_ENABLED:
        return True
    from .models import PlanFeature
    from apps.credits.models import CompanyCreditFeature
    from django.utils import timezone
    if not PlanFeature.objects.filter(key=module, is_active=True).exists():
        # Legacy installations allow unregistered modules. Strict tenant
        # rollouts deny them instead; otherwise an unregistered permission
        # would bypass a company's feature purchases.
        return not settings.CREDIT_STRICT_MODULE_GATING_ENABLED
    if settings.CREDIT_POSTPAID_FEATURE_BILLING_ENABLED:
        from apps.credits.models import FeatureUsageSession
        individually_enabled = CompanyCreditFeature.objects.filter(
            company=user.company, feature__key=module, feature__is_active=True,
            enabled=True,
        ).exists() and FeatureUsageSession.objects.filter(
            company=user.company, feature__key=module, ended_at__isnull=True,
        ).exists()
    else:
        individually_enabled = CompanyCreditFeature.objects.filter(
            company=user.company, feature__key=module, feature__is_active=True,
            enabled=True, next_charge_at__gt=timezone.now(),
        ).exists()
    if individually_enabled:
        return True

    # A company-paid postpaid feature is shared by all role-authorized users.
    # Nonpayment suspension removes access immediately without waiting for a
    # Celery cycle; the frontend never decides the permission.
    if getattr(settings, "CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED", False):
        from apps.credits.models import CompanyPostpaidFeature
        if CompanyPostpaidFeature.objects.filter(
            company=user.company, feature__key=module,
            feature__is_active=True, enabled=True, suspended=False,
        ).exists():
            return True

    # A paid label grants feature entitlement, never role permission.
    # Time-limited access stops at expiry without needing a cron job.
    # Per-use access is blocked until an authenticated server operation
    # is explicitly wired to the usage-consumption service hook.
    from django.db.models import Q
    from apps.credits.models import CompanyBundleAccess
    return CompanyBundleAccess.objects.filter(
        company=user.company, enabled=True, bundle__is_active=True,
        bundle__features__key=module, bundle__features__is_active=True,
    ).filter(
        Q(billing_type_at_purchase="permanent", valid_until__isnull=True) |
        Q(billing_type_at_purchase__in=["hourly", "daily", "weekly", "monthly"],
          valid_until__gt=timezone.now())
    ).exists()
