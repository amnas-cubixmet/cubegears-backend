"""Razorpay-hosted credit checkout reconciliation.

All facts about payment are fetched directly from Razorpay using server secrets.
Browser redirects and query parameters cannot credit the wallet.
"""
from django.db import transaction
from django.utils import timezone

from apps.saas.razorpay_client import api_request, fetch_payment
from .models import CreditPurchase
from .service import change_balance, wallet_for


def fetch_link(link_id):
    if not isinstance(link_id, str) or not link_id.startswith("plink_") or len(link_id) > 80:
        raise ValueError("Invalid hosted checkout ID.")
    return api_request("GET", "payment_links/" + link_id)


@transaction.atomic
def credit_hosted_purchase(purchase_id, link, payment):
    purchase = CreditPurchase.objects.select_for_update().select_related("company").get(pk=purchase_id)
    if not purchase.payment_link_id:
        raise ValueError("This is not a hosted checkout purchase.")
    if purchase.status == "credited":
        if purchase.payment_id != payment.get("id"):
            raise ValueError("Purchase already credited with a different payment.")
        return purchase
    if purchase.status != "pending":
        raise ValueError("This purchase is not pending.")
    payments = link.get("payments")
    ids = [item.get("payment_id") for item in payments if isinstance(item, dict)] if isinstance(payments, list) else []
    if (
        link.get("id") != purchase.payment_link_id
        or link.get("reference_id") != purchase.receipt
        or link.get("status") != "paid"
        or link.get("currency") != "INR"
        or link.get("amount") != purchase.amount_paise
        or link.get("amount_paid") != purchase.amount_paise
        or payment.get("id") not in ids
        or not str(payment.get("id") or "").startswith("pay_")
        or payment.get("status") != "captured"
        or payment.get("currency") != "INR"
        or payment.get("amount") != purchase.amount_paise
    ):
        raise ValueError("Unverified hosted payment or mismatched amount.")

    wallet_for(purchase.company)
    change_balance(
        purchase.company,
        purchase.credits,
        kind="purchase",
        reference="razorpay-link:" + purchase.payment_link_id,
        description=f"Purchased {purchase.credits} credits",
        actor=purchase.requested_by,
    )
    purchase.payment_id = payment["id"]
    purchase.status = "credited"
    purchase.paid_at = timezone.now()
    purchase.save(update_fields=["payment_id", "status", "paid_at"])
    from .monthly_policy import retry_after_topup
    retry_after_topup(purchase.company)
    return purchase


def reconcile_hosted_purchase(purchase):
    """Return pending without credit if Razorpay has not captured the payment."""
    if purchase.status != "pending" or not purchase.payment_link_id:
        return purchase
    link = fetch_link(purchase.payment_link_id)
    if (not isinstance(link, dict)
        or link.get("id") != purchase.payment_link_id
        or link.get("reference_id") != purchase.receipt):
        raise ValueError("Payment link does not belong to this purchase.")
    if link.get("status") != "paid":
        return purchase
    payments = link.get("payments") or []
    if not isinstance(payments, list):
        raise ValueError("Unexpected payment details.")
    for entry in payments:
        if (isinstance(entry, dict) and entry.get("status") == "captured"
            and str(entry.get("payment_id") or "").startswith("pay_")):
            payment = fetch_payment(entry["payment_id"])
            return credit_hosted_purchase(purchase.pk, link, payment)
    return purchase
