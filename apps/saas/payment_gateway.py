"""No third-party payment can unlock access without gateway confirmation."""
import base64
import hashlib
import hmac
import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

def credentials():
    key = os.environ.get("RAZORPAY_KEY_ID", "")
    secret = os.environ.get("RAZORPAY_KEY_SECRET", "")
    if not key or not secret:
        raise RuntimeError("Razorpay key and secret must be configured on the server.")
    return key, secret

def api_request(method, path, payload=None):
    key,secret=credentials()
    auth=base64.b64encode((key+":"+secret).encode()).decode()
    req=Request("https://api.razorpay.com/v1/"+path.lstrip("/"),
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Authorization":"Basic "+auth,"Content-Type":"application/json"},
        method=method)
    try:
        with urlopen(req,timeout=12) as resp:
            return json.load(resp)
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError("Unable to confirm payment with Razorpay.") from exc

def verify_checkout(order_id,payment_id,signature):
    _,secret=credentials()
    expected=hmac.new(secret.encode(),f"{order_id}|{payment_id}".encode(),hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected,str(signature or ""))

def verify_webhook(body,signature):
    secret=os.environ.get("RAZORPAY_WEBHOOK_SECRET","")
    if not secret: return False
    digest=hmac.new(secret.encode(),body,hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest,str(signature or ""))
