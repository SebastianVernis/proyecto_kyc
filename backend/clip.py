"""Clip API integration — Checkout Transparente + Webhook."""
import base64
import json
import os
import urllib.request
import urllib.error

API_BASE = "https://api.payclip.com"

def _get_auth_token():
    """Generate Basic auth token from API_KEY:SECRET_KEY."""
    api_key = os.environ.get("CLIP_API_KEY", "")
    secret_key = os.environ.get("CLIP_SECRET_KEY", "")
    if not api_key or not secret_key:
        return ""
    combined = api_key + ":" + secret_key
    return base64.b64encode(combined.encode()).decode()

def _get_public_key():
    return os.environ.get("CLIP_PUBLIC_KEY", "")

def create_payment(card_token_id, amount, currency="MXN", description="Suscripción Argos OSINT",
                   external_reference=None, customer_email=None, customer_phone=None,
                   webhook_url=None):
    """Create a payment using a card token from the SDK."""
    auth_token = _get_auth_token()
    if not auth_token:
        raise ValueError("CLIP_API_KEY o CLIP_SECRET_KEY no configuradas")

    body = {
        "amount": amount,
        "currency": currency,
        "description": description,
        "payment_method": {
            "token": card_token_id,
        },
    }
    if customer_email or customer_phone:
        body["customer"] = {}
        if customer_email:
            body["customer"]["email"] = customer_email
        if customer_phone:
            body["customer"]["phone"] = customer_phone
    if external_reference:
        body["external_reference"] = external_reference
    if webhook_url:
        body["webhook_url"] = webhook_url

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{API_BASE}/payments",
        data=data,
        headers={
            "Authorization": f"Basic {auth_token}",
            "Content-Type": "application/json",
            "User-Agent": "ArgosOSINT/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Clip API error {e.code}: {err_body}")

def get_payment(payment_id):
    """Get payment details by ID."""
    auth_token = _get_auth_token()
    if not auth_token:
        raise ValueError("CLIP_API_KEY o CLIP_SECRET_KEY no configuradas")
    req = urllib.request.Request(
        f"{API_BASE}/payments/{payment_id}",
        headers={"Authorization": f"Basic {auth_token}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Clip API error {e.code}: {err_body}")
