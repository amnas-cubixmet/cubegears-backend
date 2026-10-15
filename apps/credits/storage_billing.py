"""Auditable tenant storage credit billing based on real MediaFile byte totals.

One policy applies to every company. Billing is explicit and opt-in (disabled by
default), run once per billing period by a single scheduled worker. Each period
gets one ledger reference, including zero-cost periods. No invented usage.
"""
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.saas.models import MediaFile
from .models import CreditLedger, StorageCreditPolicy
from .service import change_balance, wallet_for


def storage_usage(company):
    bytes_used = MediaFile.objects.filter(company=company).aggregate(
        total=Sum("size_bytes")
    )["total"] or 0
    return int(max(0, bytes_used))


def storage_quote(company, policy=None, now=None):
    policy = policy or StorageCreditPolicy.objects.filter(is_active=True).first()
    if not policy or policy.billing_mode != "snapshot":
        return None
    if policy.credits_per_gb < 0 or policy.included_gb < 0:
        raise ValueError("Storage pricing must not contain negative rates or allowances.")
    now = now or timezone.now()
    day = timezone.localdate(now)
    period = day.isoformat() if policy.billing_period == "daily" else day.strftime("%Y-%m")
    bytes_used = storage_usage(company)
    used_gb = Decimal(bytes_used) / Decimal(1024 ** 3)
    billable_gb = max(Decimal("0"), used_gb - policy.included_gb)
    credits = (billable_gb * policy.credits_per_gb).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return {
        "period": period,
        "policy": policy,
        "bytes": bytes_used,
        "used_gb": used_gb.quantize(Decimal("0.001")),
        "billable_gb": billable_gb.quantize(Decimal("0.001")),
        "credits": credits,
        "reference": f"storage:{company.pk}:{policy.billing_period}:{period}",
    }


@transaction.atomic
def charge_storage_period(company, policy=None, now=None):
    quote = storage_quote(company, policy=policy, now=now)
    if not quote:
        return "disabled", None
    if not quote["policy"].is_active or quote["policy"].billing_mode != "snapshot":
        return "disabled", quote
    if CreditLedger.objects.filter(reference=quote["reference"], company=company).exists():
        return "already_billed", quote
    wallet_for(company)
    try:
        _, created = change_balance(
            company, -quote["credits"],
            kind="storage_charge",
            reference=quote["reference"],
            description=(
                f"Storage {quote['period']}: {quote['used_gb']} GB used; "
                f"{quote['billable_gb']} GB billable"
            ),
        )
    except ValueError:
        return "insufficient_credits", quote
    return ("charged" if created else "already_billed"), quote
