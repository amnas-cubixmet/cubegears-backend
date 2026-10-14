"""Razorpay Orders API client; private secret is never sent to the browser."""
import base64
import hashlib
import hmac
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from django.conf import settings

class GatewayError(Exception):
    pass

def configured():
    return bool(settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET)

def api_request(method, path, payload=None):
    if not configured():
        raise GatewayError("Razorpay merchant credentials are not configured.")
    credentials = (settings.RAZORPAY_KEY_ID + ":" + settings.RAZORPAY_KEY_SECRET).encode()
    encoded = base64.b64encode(credentials).decode()
    req = Request("https://api.razorpay.com/v1/" + path.lstrip("/"),
                  data=json.dumps(payload).encode() if payload is not None else None,
                  method=method,
                  headers={"Authorization": "Basic " + encoded, "Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=12) as response:
            return json.loads(response.read(131072))
    except (HTTPError, URLError, OSError, ValueError) as exc:
        raise GatewayError("Razorpay rejected the request or is unavailable.") from exc

def create_order(amount_paise, receipt, company_id):
    return api_request("POST", "orders", {
        "amount": amount_paise, "currency": "INR", "receipt": receipt,
        "partial_payment": False, "notes": {"company_id": str(company_id)},
    })

def fetch_payment(payment_id):
    if not isinstance(payment_id, str) or not payment_id.startswith("pay_") or len(payment_id) > 80:
        raise GatewayError("Invalid payment identifier.")
    return api_request("GET", "payments/" + payment_id)

def verify_checkout(order_id, payment_id, signature):
    expected = hmac.new(settings.RAZORPAY_KEY_SECRET.encode(),
                        (order_id + "|" + payment_id).encode(), hashlib.sha256).hexdigest()
    return isinstance(signature, str) and hmac.compare_digest(expected, signature)

def verify_webhook(body, signature):
    secret = settings.RAZORPAY_WEBHOOK_SECRET
    if not secret or not isinstance(signature, str):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
