"""Cash/Bank payment channel consistency without rewriting historical methods.

Legacy UPI and Wallet account kinds are digital and display under Bank in
the React finance workspace. Do not change their stored kinds or ledger rows.
"""
from rest_framework.exceptions import ValidationError


def account_channel(kind):
    text = str(kind or "").strip().lower()
    if text == "cash":
        return "Cash"
    if text in {"bank", "upi", "wallet"}:
        return "Bank"
    return None


def validate_payment_channel(method, account):
    """New Cash/Bank postings must debit/credit a matching account.

    Preserve legacy UPI, cheque and card transaction representations from
    older workshop modules. Their source-of-truth ledger is unchanged.
    """
    if method not in {"Cash", "Bank"}:
        return
    actual = account_channel(getattr(account, "kind", None))
    if actual != method:
        raise ValidationError({
            "account": f"Choose an active {method} account for this payment method."
        })
