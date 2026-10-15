"""Atomic, tenant-scoped credits. Client never controls a balance or price."""
from datetime import timedelta
from decimal import Decimal
from django.db import transaction
from django.conf import settings
from django.db.models import F
from django.utils import timezone

from apps.employees.models import Employee
from apps.accounts.models import User
from .models import (
    CreditWallet, CreditLedger, FeatureCreditPrice, CompanyCreditFeature,
    CreditPurchase,
)

ZERO = Decimal("0.00")
PERIOD_DAYS = 30
BILLING_DURATIONS = {"daily": timedelta(days=1), "weekly": timedelta(days=7), "monthly": timedelta(days=30)}


def staff_count(company):
    """Bill active Employee records, not admin accounts or unauthenticated guesses."""
    return Employee.objects.filter(company=company, status__iexact="Active").count()


def wallet_for(company):
    wallet, _ = CreditWallet.objects.get_or_create(company=company)
    return wallet


def billable_user_count(company):
    """Active tenant login accounts; excludes platform staff and inactive logins."""
    return User.objects.filter(company=company, is_active=True).count()


def price_for(feature_price, staff, users=0):
    """Recurring charge is separate from the one-time activation fee."""
    return (
        feature_price.base_credits
        + feature_price.per_staff_credits * staff
        + feature_price.per_user_credits * users
    )


def period_duration(feature_price):
    return BILLING_DURATIONS[feature_price.billing_period]


def change_balance(company, delta, *, kind, reference, description, actor=None, allow_negative=False):
    """Call from transaction.atomic(). Reference uniqueness prevents double charging."""
    existing = CreditLedger.objects.filter(reference=reference).first()
    if existing:
        if existing.company_id != company.pk:
            raise ValueError("Conflicting credit reference.")
        return existing, False
    wallet = CreditWallet.objects.select_for_update().get(company=company)
    next_balance = wallet.balance + Decimal(delta)
    if next_balance < ZERO and not allow_negative:
        raise ValueError("Insufficient credits. Buy credits to continue.")
    wallet.balance = next_balance
    wallet.save(update_fields=["balance", "updated_at"])
    event = CreditLedger.objects.create(
        company=company, kind=kind, description=description[:255],
        delta=delta, balance_after=next_balance,
        actor=actor, reference=reference,
    )
    return event, True


@transaction.atomic
def adjust_company_balance(company, delta, *, reason, actor, reference):
    """Platform staff only; caller must separately enforce platform RBAC."""
    if not reason.strip() or not Decimal("-1000000") <= delta <= Decimal("1000000"):
        raise ValueError("Credit adjustment requires a reason and bounded amount.")
    wallet_for(company)
    event, _ = change_balance(
        company, delta, kind="adjustment", reference=reference,
        description=reason, actor=actor,
    )
    return event


@transaction.atomic
def enable_paid_feature(company, feature, actor):
    if getattr(settings, 'CREDIT_LABEL_ONLY_ACCESS_ENABLED', False):
        raise ValueError('Activate a Feature Label instead of a standalone module.')
    pricing = FeatureCreditPrice.objects.select_related("feature").filter(
        feature=feature, feature__is_active=True, is_available=True,
    ).first()
    if not pricing:
        raise ValueError("This feature is not enabled by CubeGear platform staff.")
    wallet_for(company)
    state, _ = CompanyCreditFeature.objects.select_for_update().get_or_create(
        company=company, feature=feature,
    )
    now = timezone.now()
    if settings.CREDIT_POSTPAID_FEATURE_BILLING_ENABLED:
        if pricing.billing_period != "monthly":
            raise ValueError("Postpaid usage requires a monthly feature rate.")
        from .models import FeatureUsageSession
        active_session = FeatureUsageSession.objects.filter(
            company=company, feature=feature, ended_at__isnull=True
        ).first()
        if state.enabled:
            if active_session:
                return state, ZERO
            raise ValueError("Existing prepaid entitlement must be migrated before postpaid activation.")
        users = billable_user_count(company)
        staff = staff_count(company)
        FeatureUsageSession.objects.create(
            company=company, feature=feature,
            started_at=now,
            monthly_credits=price_for(pricing, staff, users),
            activation_credits=pricing.activation_credits,
            user_seats=users, staff_seats=staff,
        )
        state.enabled = True
        state.next_charge_at = None
        state.save(update_fields=["enabled", "next_charge_at", "updated_at"])
        return state, ZERO
    if state.enabled and state.next_charge_at and state.next_charge_at > now:
        return state, ZERO
    staff = staff_count(company)
    users = billable_user_count(company)
    charge = price_for(pricing, staff, users) + pricing.activation_credits
    change_balance(
        company, -charge, kind="feature_charge",
        reference=f"feature-start:{state.pk}:{timezone.now().isoformat()}",
        description=f"Enable {feature.label}: {users} users, {staff} employees; activation + {pricing.billing_period} rate",
        actor=actor,
    )
    state.enabled = True
    state.next_charge_at = now + period_duration(pricing)
    state.save(update_fields=["enabled", "next_charge_at", "updated_at"])
    return state, charge


@transaction.atomic
def disable_paid_feature(company, feature, actor):
    state = CompanyCreditFeature.objects.select_for_update().filter(company=company, feature=feature).first()
    if not state or not state.enabled:
        return
    if settings.CREDIT_POSTPAID_FEATURE_BILLING_ENABLED:
        from .models import FeatureUsageSession
        FeatureUsageSession.objects.select_for_update().filter(
            company=company, feature=feature, ended_at__isnull=True,
        ).update(ended_at=timezone.now())
    state.enabled = False
    state.next_charge_at = None
    state.save(update_fields=["enabled", "next_charge_at", "updated_at"])
    wallet_for(company)
    change_balance(
        company, ZERO, kind="feature_disabled",
        reference=f"feature-stop:{state.pk}:{timezone.now().isoformat()}",
        description=f"Disabled {feature.label}. Already charged periods are not refunded.",
        actor=actor,
    )


@transaction.atomic
def renew_feature(state):
    if getattr(settings, 'CREDIT_LABEL_ONLY_ACCESS_ENABLED', False):
        return 'label_only'
    if settings.CREDIT_POSTPAID_FEATURE_BILLING_ENABLED:
        # Never perform prepaid due-date charge while monthly usage mode runs.
        return "postpaid"
    state = CompanyCreditFeature.objects.select_for_update().get(pk=state.pk)
    if not state.enabled or not state.next_charge_at or state.next_charge_at > timezone.now():
        return None
    pricing = FeatureCreditPrice.objects.filter(
        feature=state.feature, is_available=True, feature__is_active=True
    ).first()
    if not pricing:
        state.enabled = False
        state.next_charge_at = None
        state.save(update_fields=["enabled", "next_charge_at", "updated_at"])
        return "unavailable"
    count = staff_count(state.company)
    users = billable_user_count(state.company)
    cost = price_for(pricing, count, users)
    wallet_for(state.company)
    ref = f"feature-renew:{state.pk}:{state.next_charge_at.isoformat()}"
    try:
        change_balance(
            state.company, -cost, kind="feature_charge",
            reference=ref,
            description=f"Renew {state.feature.label}: {users} users, {count} employees / {pricing.billing_period}",
        )
    except ValueError:
        state.enabled = False
        state.next_charge_at = None
        state.save(update_fields=["enabled", "next_charge_at", "updated_at"])
        change_balance(
            state.company, ZERO, kind="feature_suspended",
            reference=ref + ":suspend",
            description=f"{state.feature.label} suspended: insufficient wallet credits",
        )
        return "suspended"
    state.next_charge_at = timezone.now() + period_duration(pricing)
    state.save(update_fields=["next_charge_at", "updated_at"])
    return "renewed"


@transaction.atomic
def confirm_purchase(purchase_id, payment):
    """Only verified Razorpay captured payment/amount/order releases credits once."""
    purchase = CreditPurchase.objects.select_for_update().select_related("company").get(pk=purchase_id)
    if purchase.status == "credited":
        if purchase.payment_id != payment.get("id"):
            raise ValueError("Order belongs to a different payment.")
        return purchase
    if purchase.status != "pending" or not purchase.order_id:
        raise ValueError("Purchase is not pending.")
    if (payment.get("order_id") != purchase.order_id
        or payment.get("amount") != purchase.amount_paise
        or payment.get("currency") != "INR"
        or payment.get("status") != "captured"
        or not str(payment.get("id", "")).startswith("pay_")):
        raise ValueError("Payment was not captured for the matching server-created order.")

    wallet_for(purchase.company)
    change_balance(
        purchase.company, purchase.credits, kind="purchase",
        reference=f"razorpay:{purchase.order_id}",
        description=f"Purchased {purchase.credits} credits",
        actor=purchase.requested_by,
    )
    purchase.payment_id = payment["id"]
    purchase.status = "credited"
    purchase.paid_at = timezone.now()
    purchase.save(update_fields=["payment_id", "status", "paid_at"])
    return purchase


# Bundles use the same transactional wallet ledger as the existing 30-day features.
# Existing single-feature pricing and subscriptions are unaffected.
from uuid import uuid4
from .models import FeatureBundle, CompanyBundleAccess


def bundle_is_usable(access, now=None):
    """Expired packages never grant access; permanent packages do not expire."""
    now = now or timezone.now()
    if not access.enabled or not access.bundle.is_active:
        return False
    if access.billing_type_at_purchase == "per_use":
        return access.uses_remaining > 0
    return access.valid_until is None or access.valid_until > now


@transaction.atomic
def purchase_feature_bundle(company, bundle, actor):
    """Charge the *server's* published credit price and grant scoped entitlement.

    Weekly/daily/hourly purchases extend from the unexpired end; permanent
    purchases are not charged twice. Per-use purchases prepay exactly one
    usage token; only trusted server operations may consume that token.
    """
    bundle = FeatureBundle.objects.get(pk=bundle.pk, is_active=True)
    from django.conf import settings
    if getattr(settings, "CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED", False):
        from .models import CompanyPostpaidFeature
        if CompanyPostpaidFeature.objects.filter(
            company=company, enabled=True, feature__credit_bundles=bundle,
        ).exists():
            raise ValueError("This package overlaps an active monthly-postpaid feature. Disable it first.")
    if not bundle.features.filter(is_active=True).exists():
        raise ValueError("This label has no active features.")
    if not Decimal("0.01") <= bundle.per_user_credits <= Decimal("1000000.00"):
        raise ValueError("Invalid per-user credit price for this label.")
    wallet_for(company)
    now = timezone.now()
    access, _ = CompanyBundleAccess.objects.select_for_update().get_or_create(
        company=company, bundle=bundle,
        defaults={"enabled": False},
    )
    if access.billing_type_at_purchase == "permanent" and access.enabled and access.valid_until is None:
        return access, ZERO

    users = billable_user_count(company)
    # Single clear rate: no base charge, activation fee, or employee-seat fee.
    # The user count is read from authenticated company accounts, never from JS.
    price = bundle.per_user_credits * users
    if not Decimal("0.01") <= price <= Decimal("1000000.00"):
        raise ValueError("Bundle charge must be between 0.01 and 1,000,000 credits.")
    # Price and authorization are never accepted from the browser.
    change_balance(
        company, -price, kind="bundle_charge",
        reference=f"bundle-purchase:{uuid4().hex}",
        description=f"Feature label: {bundle.name} ({bundle.billing_type}); {users} login users",
        actor=actor,
    )
    if bundle.billing_type == "per_use":
        access.uses_remaining += 1
        access.valid_until = None
    elif bundle.billing_type == "permanent":
        access.uses_remaining = 0
        access.valid_until = None
    else:
        periods = {"hourly": timedelta(hours=1), "daily": timedelta(days=1),
                   "weekly": timedelta(days=7), "monthly": timedelta(days=30)}
        if bundle.billing_type not in periods:
            raise ValueError("Invalid billing period.")
        start = max(now, access.valid_until) if access.enabled and access.valid_until else now
        access.valid_until = start + periods[bundle.billing_type]
        access.uses_remaining = 0
    access.enabled = True
    access.last_price_credits = price
    access.billing_type_at_purchase = bundle.billing_type
    access.purchased_at = now
    access.save(update_fields=[
        "enabled", "valid_until", "uses_remaining", "last_price_credits",
        "billing_type_at_purchase", "purchased_at", "updated_at",
    ])
    return access, price


@transaction.atomic
def disable_feature_bundle(company, bundle):
    """Stop access, without changing purchases or refunding prepaid credits."""
    access = CompanyBundleAccess.objects.select_for_update().filter(
        company=company, bundle=bundle,
    ).first()
    if access and access.enabled:
        access.enabled = False
        access.save(update_fields=["enabled", "updated_at"])
    return access


@transaction.atomic
def consume_bundle_use(company, bundle, feature, *, operation_ref, actor=None):
    """Backend-only hook: call within the actual metered operation's transaction.

    This is deliberately NOT exposed as a browser API: browser-reported usage
    is untrusted. Hook to a real server-side completed operation with a stable
    unique operation_ref; the caller must enforce RBAC independently.
    """
    if not operation_ref or len(str(operation_ref)) > 110:
        raise ValueError("A stable operation reference is required.")
    reference = f"bundle-use:{company.pk}:{operation_ref}"
    if CreditLedger.objects.filter(reference=reference, company=company).exists():
        return False
    access = CompanyBundleAccess.objects.select_for_update().select_related("bundle").filter(
        company=company, bundle=bundle,
    ).first()
    if (not access or access.billing_type_at_purchase != "per_use"
        or not bundle_is_usable(access)
        or not access.bundle.features.filter(pk=feature.pk, is_active=True).exists()):
        raise ValueError("No prepaid use is available for this feature.")
    access.uses_remaining -= 1
    if access.uses_remaining == 0:
        access.enabled = False
    access.save(update_fields=["uses_remaining", "enabled", "updated_at"])
    wallet_for(company)
    change_balance(
        company, ZERO, kind="bundle_use", reference=reference,
        description=f"Used {feature.label} via {bundle.name}",
        actor=actor,
    )
    return True
