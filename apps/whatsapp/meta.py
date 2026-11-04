"""Official Meta WhatsApp Cloud API integration, no WhatsApp Web automation."""
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from cryptography.fernet import Fernet
from django.conf import settings

GRAPH_HOST = "https://graph.facebook.com"


def configured():
    return bool(
        getattr(settings, "WHATSAPP_META_APP_ID", "")
        and getattr(settings, "WHATSAPP_META_APP_SECRET", "")
        and getattr(settings, "WHATSAPP_TOKEN_ENCRYPTION_KEY", "")
    )


def _cipher():
    key = getattr(settings, "WHATSAPP_TOKEN_ENCRYPTION_KEY", "")
    if not key:
        raise ValueError("WhatsApp credential encryption is not configured.")
    return Fernet(key.encode())


def encrypt_token(access_token):
    if not access_token:
        raise ValueError("Missing official Meta access token.")
    return _cipher().encrypt(access_token.encode()).decode()


def decrypt_token(encrypted):
    return _cipher().decrypt(encrypted.encode()).decode()


def _graph_url(path):
    version = getattr(settings, "WHATSAPP_GRAPH_VERSION", "v25.0")
    if not version.startswith("v") or not version[1:].replace(".", "").isdigit():
        raise ValueError("Invalid Meta Graph API version.")
    return f"{GRAPH_HOST}/{version}/{path.lstrip('/')}"


def graph_request(path, token=None, method="GET", data=None):
    url = _graph_url(path)
    if method == "GET" and data:
        url = f"{url}?{urlencode(data)}"
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = None
    if method != "GET":
        body = json.dumps(data or {}).encode()
        headers["Content-Type"] = "application/json"
    req = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(req, timeout=15) as response:
            return json.loads(response.read(65536).decode())
    except (HTTPError, URLError, OSError, ValueError) as exc:
        # Never log the bearer token, request payload or secret OAuth code.
        raise ValueError("Official Meta WhatsApp API request failed. Verify app configuration and business permissions.") from exc


def exchange_embedded_signup_code(code):
    if not configured():
        raise ValueError("Meta app credentials and encryption are not configured.")
    return graph_request("oauth/access_token", data={
        "client_id": settings.WHATSAPP_META_APP_ID,
        "client_secret": settings.WHATSAPP_META_APP_SECRET,
        "code": code,
    })


def verify_waba_phone(waba_id, phone_id, token):
    phones = graph_request(f"{waba_id}/phone_numbers", token=token, data={"fields": "id,display_phone_number"})
    phone = next((p for p in phones.get("data", []) if str(p.get("id")) == str(phone_id)), None)
    if not phone:
        raise ValueError("This phone number is not attached to the selected Meta WhatsApp Business Account.")
    graph_request(f"{waba_id}/subscribed_apps", token=token, method="POST", data={})
    return phone.get("display_phone_number") or ""


def approved_templates(waba_id, token):
    payload = graph_request(f"{waba_id}/message_templates", token=token, data={"fields": "name,language,status", "limit": 250})
    return [row for row in payload.get("data", []) if row.get("status") == "APPROVED"]


def send_template_message(phone_id, token, recipient, template, language, vehicle, job, link):
    payload = {
        "messaging_product": "whatsapp",
        "to": recipient,
        "type": "template",
        "template": {
            "name": template,
            "language": {"code": language},
            # Approved templates must declare exactly these three body values.
            "components": [{"type": "body", "parameters": [
                {"type": "text", "text": vehicle},
                {"type": "text", "text": job},
                {"type": "text", "text": link},
            ]}],
        },
    }
    return graph_request(f"{phone_id}/messages", token=token, method="POST", data=payload)
