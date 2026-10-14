"""Trusted, idempotent SaaS subscription activation."""
from datetime import timedelta
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from .models import Subscription, RazorpayPlanPayment

def paid_subscription(company):
    if not company:
        return None
    return Subscription.objects.filter(
        company=company, status__iexact="Active",
        renews_at__gt=timezone.now(),
        razorpay_payments__status="captured",
    ).select_related("plan_definition").order_by("-created_at").first()

def access_state(company):
    from django.conf import settings
    active = paid_subscription(company)
    return {
        "enforcementEnabled": bool(settings.SAAS_PAYMENT_ENFORCEMENT_ENABLED),
        "allowed": bool(active) or not settings.SAAS_PAYMENT_ENFORCEMENT_ENABLED,
        "status": "active" if active else "payment_required",
        "plan": active.plan_definition.name if active and active.plan_definition else None,
        "expiresAt": active.renews_at.isoformat() if active else None,
        "billingCycle": active.billing_cycle if active else None,
    }

@transaction.atomic
def capture_order(payment_record, gateway_payment):
    record = RazorpayPlanPayment.objects.select_for_update().select_related("plan", "company").get(pk=payment_record.pk)
    if record.status == "captured":
        if record.razorpay_payment_id != gateway_payment.get("id"):
            raise ValueError("Order was captured with a different payment.")
        return record
    if record.status != "pending":
        raise ValueError("Order is no longer payable.")
    if (
        gateway_payment.get("order_id") != record.razorpay_order_id
        or not record.razorpay_order_id
        or gateway_payment.get("status") != "captured"
        or gateway_payment.get("currency") != record.currency
        or gateway_payment.get("amount") != record.amount_paise
        or not str(gateway_payment.get("id", "")).startswith("pay_")
    ):
        raise ValueError("Payment data does not match the stored order.")

    now = timezone.now()
    previous = Subscription.objects.filter(
        company=record.company, status__iexact="Active", renews_at__gt=now,
    ).order_by("-created_at").first()
    start = max(now, previous.renews_at) if previous and previous.plan_definition_id == record.plan_id else now
    end = start + timedelta(days=record.validity_days)
    Subscription.objects.filter(company=record.company, status__iexact="Active").update(status="Expired")
    subscription = Subscription.objects.create(
        company=record.company, plan_definition=record.plan,
        plan=record.plan.name, status="Active", billing_cycle=record.cycle,
        amount=Decimal(record.amount_paise) / Decimal(100),
        currency=record.currency, starts_at=now, renews_at=end,
        seats=record.plan.max_users,
    )
    record.subscription = subscription
    record.razorpay_payment_id = gateway_payment["id"]
    record.payment_method = str(gateway_payment.get("method") or "")[:30]
    record.status = "captured"
    record.paid_at = now
    record.period_start = start
    record.period_end = end
    record.save(update_fields=[
        "subscription", "razorpay_payment_id", "payment_method", "status",
        "paid_at", "period_start", "period_end", "updated_at",
    ])
    return record

@transaction.atomic
def refund_order(payment_record):
    record = RazorpayPlanPayment.objects.select_for_update().get(pk=payment_record.pk)
    if record.status != "captured":
        return record
    record.status = "refunded"
    record.save(update_fields=["status", "updated_at"])
    if record.subscription_id:
        Subscription.objects.filter(
            pk=record.subscription_id, company=record.company,
            status__iexact="Active",
        ).update(status="Cancelled")
    return record

def public_payment(record):
    return {
        "id": str(record.pk), "plan": record.plan.name,
        "billingCycle": record.cycle, "validityDays": record.validity_days,
        "amount": str(Decimal(record.amount_paise) / Decimal(100)),
        "currency": record.currency, "status": record.status,
        "orderId": record.razorpay_order_id,
        "paymentId": record.razorpay_payment_id,
        "method": record.payment_method,
        "paidAt": record.paid_at.isoformat() if record.paid_at else None,
        "validUntil": record.period_end.isoformat() if record.period_end else None,
        "createdAt": record.created_at.isoformat(),
    }
