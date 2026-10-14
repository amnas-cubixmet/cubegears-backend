from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from .models import Subscription
from .payment_models import PaymentPolicy, SubscriptionPayment
from .payment_gateway import api_request

def has_paid_access(company):
    if not company: return False
    now=timezone.now()
    return Subscription.objects.filter(company=company,status__iexact="Active",
        renews_at__gt=now,plan_definition__is_active=True).exists()

def activate_captured(order_id,payment_id):
    """Idempotent, verified activation; never trust user-submitted amounts."""
    data=api_request("GET",f"payments/{payment_id}")
    if (data.get("status")!="captured" or data.get("order_id")!=order_id
            or data.get("currency")!="INR"):
        raise ValueError("Payment is not captured for this order.")
    with transaction.atomic():
        entry=SubscriptionPayment.objects.select_for_update().select_related("plan","company").get(order_id=order_id)
        if int(data.get("amount",0))!=entry.amount_paise:
            raise ValueError("Amount mismatch.")
        if entry.status=="captured":
            if entry.payment_id!=payment_id:
                raise ValueError("Order already captured with a different payment.")
            return entry
        if entry.payment_id and entry.payment_id!=payment_id:
            raise ValueError("Different payment already attached to order.")
        now=timezone.now()
        policy=PaymentPolicy.current()
        days=policy.yearly_access_days if entry.billing_cycle=="yearly" else policy.monthly_access_days
        if not 1<=days<=3660:
            raise ValueError("Invalid subscription term configuration.")
        old=Subscription.objects.filter(company=entry.company,status__iexact="Active").order_by("-renews_at").first()
        start=max(now,old.renews_at) if old and old.renews_at else now
        until=start+timedelta(days=days)
        Subscription.objects.filter(company=entry.company,status__iexact="Active").update(status="Expired")
        Subscription.objects.create(company=entry.company,branch=entry.branch,
            plan=entry.plan.name,status="Active",plan_definition=entry.plan,
            billing_cycle=entry.billing_cycle,amount=entry.amount_paise/100,
            starts_at=start,renews_at=until,seats=entry.plan.max_users)
        entry.status="captured"
        entry.payment_id=payment_id
        entry.gateway_method=str(data.get("method") or "")[:50]
        entry.paid_at=now
        entry.access_until=until
        entry.gateway_data={"status":"captured","method":entry.gateway_method}
        entry.save(update_fields=["status","payment_id","gateway_method","paid_at","access_until","gateway_data","updated_at"])
        return entry
