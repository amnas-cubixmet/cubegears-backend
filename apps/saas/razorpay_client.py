"""Razorpay Orders/Payments API client.

Keep merchant credentials server-side. Errors expose only a safe category and
HTTP status, never Basic Auth headers, credentials or payment payloads.
"""
import base64
import hashlib
import hmac
import json
import logging
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings

logger = logging.getLogger(__name__)


class GatewayError(Exception):
    """Safe gateway error; details of upstream responses are never surfaced."""

    def __init__(self, message="Razorpay is unavailable.", *, reason="gateway_error", status_code=None):
        super().__init__(message)
        self.reason = reason
        self.status_code = status_code


def configured():
    return bool(settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET)


def api_request(method, path, payload=None):
    if not configured():
        raise GatewayError("Razorpay credentials are not configured.", reason="missing_credentials")
    credentials = (settings.RAZORPAY_KEY_ID + ":" + settings.RAZORPAY_KEY_SECRET).encode("utf-8")
    encoded = base64.b64encode(credentials).decode("ascii")
    req = Request(
        "https://api.razorpay.com/v1/" + path.lstrip("/"),
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        method=method,
        headers={"Authorization": "Basic " + encoded, "Content-Type": "application/json"},
    )
    try:
        with urlopen(req, timeout=12) as response:
            return json.loads(response.read(131072))
    except HTTPError as exc:
        # HTTPError may contain a provider response body, URL and headers.
        # Never log, return or stringify those potentially sensitive details.
        reason = "invalid_credentials" if exc.code in (401, 403) else "provider_rejected"
        logger.warning("Razorpay API request failed: reason=%s status=%d", reason, exc.code)
        raise GatewayError("Razorpay rejected the API request.", reason=reason, status_code=exc.code) from None
    except (URLError, TimeoutError, OSError):
        logger.warning("Razorpay API request failed: reason=network")
        raise GatewayError("Razorpay cannot be reached.", reason="network") from None
    except ValueError:
        logger.warning("Razorpay API request failed: reason=invalid_response")
        raise GatewayError("Razorpay returned an invalid response.", reason="invalid_response") from None


def create_order(amount_paise, receipt, company_id):
    return api_request("POST", "orders", {
        "amount": amount_paise,
        "currency": "INR",
        "receipt": receipt,
        "partial_payment": False,
        "notes": {"company_id": str(company_id)},
    })


def fetch_payment(payment_id):
    if not isinstance(payment_id, str) or not payment_id.startswith("pay_") or len(payment_id) > 80:
        raise GatewayError("Invalid payment identifier.", reason="invalid_payment_id")
    return api_request("GET", "payments/" + payment_id)


def verify_checkout(order_id, payment_id, signature):
    expected = hmac.new(
        settings.RAZORPAY_KEY_SECRET.encode("utf-8"),
        (order_id + "|" + payment_id).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return isinstance(signature, str) and hmac.compare_digest(expected, signature)


def verify_webhook(body, signature):
    secret = settings.RAZORPAY_WEBHOOK_SECRET
    if not secret or not isinstance(signature, str):
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
