"""Plan entitlement checks. Legacy subscriptions stay compatible until linked to a plan."""
from rest_framework.exceptions import ValidationError
from .models import Subscription

# Always included for every active tenant, subject to the role's explicit permissions.
# `billing` remains company-owner-only in user_has_permission().
FREE_TENANT_MODULES = frozenset({"dashboard", "settings", "staff", "roles", "billing"})


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
    ).values_list("bundle__features__key", flat=True).distinct()) - FREE_TENANT_MODULES



def feature_allowed(user, permission):
    """Paid feature access never grants tenant RBAC; it can only restrict it."""
    from django.conf import settings
    module = permission.split(".")[0]
    strict_company = user.company.require_paid_features
    # Past-due companies keep only owner recovery, balance and read-only audit.
    # Check BEFORE free modules and paid labels so Dashboard itself is denied.
    from apps.credits.calendar_billing import overdue_statements
    if module not in {"billing", "company"} and overdue_statements(user.company):
        return False
    if module not in FREE_TENANT_MODULES | {"company"}:
        from apps.credits.calendar_billing import active_policy
        policy = active_policy()
        if policy and not policy.allow_zero_balance_usage:
            from apps.credits.models import CreditWallet
            balance = CreditWallet.objects.filter(company=user.company).values_list("balance", flat=True).first()
            if balance is None or balance <= 0:
                return False
    # Dashboard, Settings, Staff Management, Roles & Permissions and SaaS Account are free in EVERY billing
    # mode, including strict paid-label-only mode. RBAC/tenant checks run first
    # in user_has_permission: this never gives employees owner/billing access.
    if module in FREE_TENANT_MODULES:
        return True
    # Tenant storage accounting is an owner-only SaaS Account subsection,
    # not a fourth free workshop module. RBAC runs first in
    # user_has_permission(); staff do not inherit these permissions.
    if permission == "storage.view":
        from apps.accounts.permissions import is_company_owner
        if is_company_owner(user):
            return True
    # Account purchase/recovery controls do not require a paid module.
    if strict_company and module == "company":
        return True
    # Legacy rollout compatibility.
    if not strict_company and module in {"dashboard", "billing", "settings", "company", "storage",
                  "notifications", "roles"}:
        return True

    # Strict label-only mode is OPT-IN. Fail closed if it is enabled without
    # the paid-access enforcement switch; never treat a missing config as a grant.
    if getattr(settings, "CREDIT_LABEL_ONLY_ACCESS_ENABLED", False):
        return bool((strict_company or settings.CREDIT_FEATURE_ENFORCEMENT_ENABLED)
                    and module in active_label_modules(user.company))

    if not strict_company and not settings.CREDIT_FEATURE_ENFORCEMENT_ENABLED:
        return True
    if not strict_company and permission == "attendance.self" and not settings.CREDIT_STRICT_MODULE_GATING_ENABLED:
        return True
    from .models import PlanFeature
    from apps.credits.models import CompanyCreditFeature
    from django.utils import timezone
    if not PlanFeature.objects.filter(key=module, is_active=True).exists():
        # Legacy installations allow unregistered modules. Strict tenant
        # rollouts deny them instead; otherwise an unregistered permission
        # would bypass a company's feature purchases.
        return not (strict_company or settings.CREDIT_STRICT_MODULE_GATING_ENABLED)
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
        from apps.credits.models import CompanyPostpaidFeature, ModulePriceLabel
        if ModulePriceLabel.objects.filter(feature__key=module, monthly_postpaid=True, bundle__is_active=False).exists():
            return False
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
