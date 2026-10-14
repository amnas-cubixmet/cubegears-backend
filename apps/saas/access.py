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
        return True
    return CompanyCreditFeature.objects.filter(
        company=user.company, feature__key=module, feature__is_active=True,
        enabled=True, next_charge_at__gt=timezone.now(),
    ).exists()
