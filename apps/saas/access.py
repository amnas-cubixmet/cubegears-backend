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


def feature_allowed(user, permission):
    """Paid feature access never grants tenant RBAC; it can only restrict it."""
    from django.conf import settings
    if not settings.CREDIT_FEATURE_ENFORCEMENT_ENABLED:
        return True
    module = permission.split(".")[0]
    # Core pages, profile and purchase/credit management remain accessible.
    if module in {"dashboard", "billing", "settings", "company", "storage",
                  "notifications", "roles"} or permission == "attendance.self":
        return True
    from .models import PlanFeature
    from apps.credits.models import CompanyCreditFeature
    from django.utils import timezone
    if not PlanFeature.objects.filter(key=module, is_active=True).exists():
        # Strict mode is an explicit migration/rollout choice. It prevents
        # active API routes from accidentally bypassing paid entitlements
        # just because their catalog entry was never seeded.
        return not getattr(settings, "CREDIT_FEATURE_STRICT_GATE_ENABLED", False)
    individually_enabled = CompanyCreditFeature.objects.filter(
        company=user.company, feature__key=module, feature__is_active=True,
        enabled=True, next_charge_at__gt=timezone.now(),
    ).exists()
    if individually_enabled:
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
