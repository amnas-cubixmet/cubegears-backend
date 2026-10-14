"""Atomic, tenant-scoped credits. Client never controls a balance or price."""
from datetime import timedelta
from decimal import Decimal
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.employees.models import Employee
from .models import (
    CreditWallet, CreditLedger, FeatureCreditPrice, CompanyCreditFeature,
    CreditPurchase,
)

ZERO = Decimal("0.00")
PERIOD_DAYS = 30


def staff_count(company):
    """Bill active Employee records, not admin accounts or unauthenticated guesses."""
    return Employee.objects.filter(company=company, status__iexact="Active").count()


def wallet_for(company):
    wallet, _ = CreditWallet.objects.get_or_create(company=company)
    return wallet


def price_for(feature_price, staff):
    return feature_price.base_credits + feature_price.per_staff_credits * staff


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
    pricing = FeatureCreditPrice.objects.select_related("feature").filter(
        feature=feature, feature__is_active=True, is_available=True,
    ).first()
    if not pricing:
        raise ValueError("This feature is not enabled by CubeGear platform staff.")
    wallet_for(company)
    state, _ = CompanyCreditFeature.objects.select_for_update().get_or_create(
        company=company, feature=feature,
    )
    if state.enabled:
        return state, ZERO
    staff = staff_count(company)
    charge = price_for(pricing, staff)
    change_balance(
        company, -charge, kind="feature_charge",
        reference=f"feature-start:{state.pk}:{timezone.now().isoformat()}",
        description=f"Enable {feature.label}: {staff} staff / {PERIOD_DAYS} days",
        actor=actor,
    )
    state.enabled = True
    state.next_charge_at = timezone.now() + timedelta(days=PERIOD_DAYS)
    state.save(update_fields=["enabled", "next_charge_at", "updated_at"])
    return state, charge


@transaction.atomic
def disable_paid_feature(company, feature, actor):
    state = CompanyCreditFeature.objects.select_for_update().filter(company=company, feature=feature).first()
    if not state or not state.enabled:
        return
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
    cost = price_for(pricing, count)
    wallet_for(state.company)
    ref = f"feature-renew:{state.pk}:{state.next_charge_at.isoformat()}"
    try:
        change_balance(
            state.company, -cost, kind="feature_charge",
            reference=ref,
            description=f"Renew {state.feature.label}: {count} staff / {PERIOD_DAYS} days",
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
    state.next_charge_at = timezone.now() + timedelta(days=PERIOD_DAYS)
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
