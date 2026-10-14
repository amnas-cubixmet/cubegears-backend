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
    """An enabled plan can restrict a module, never grant role permissions.

    Feature keys match permission prefix, e.g. jobs / customers / staff.
    Compliance billing and account management remain available on every plan.
    """
    module = permission.split(".")[0]
    if module in {"invoices", "billing", "settings", "company", "storage"} or permission == "attendance.self":
        return True
    cache = getattr(user, "_saas_feature_access", None)
    if cache is None:
        plan = active_plan(user.company)
        if not plan:
            cache = None
        else:
            cache = {item.feature.key: item.enabled for item in plan.feature_access.select_related("feature")}
        user._saas_feature_access = cache if cache is not None else {}
        user._saas_plan_configured = plan is not None
    else:
        pass
    if not getattr(user, "_saas_plan_configured", False):
        return True
    # An unconfigured code retains legacy behaviour, but a listed key with
    # no active entitlement defaults off. Explicit disabled entitlement denies.
    from .models import PlanFeature
    if not hasattr(user, "_saas_feature_keys"):
        user._saas_feature_keys = set(PlanFeature.objects.filter(is_active=True).values_list("key", flat=True))
    if module not in user._saas_feature_keys:
        return True
    return user._saas_feature_access.get(module, False)
