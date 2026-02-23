# app/services/paystack_service.py
from __future__ import annotations

import hashlib
import hmac
from typing import Any, Dict, Optional
from uuid import uuid4

import requests

from app.core.config import (
    PAYSTACK_SECRET_KEY,
    PAYSTACK_CURRENCY,
    PAYSTACK_CALLBACK_URL,
)

PAYSTACK_BASE = "https://api.paystack.co"


class PaystackError(RuntimeError):
    pass


def _require_secret() -> str:
    key = (PAYSTACK_SECRET_KEY or "").strip()
    if not key:
        raise PaystackError("PAYSTACK_SECRET_KEY not configured")
    return key


def _headers() -> Dict[str, str]:
    key = _require_secret()
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "naijatax-guide/1.0",
    }


def create_reference(prefix: str = "NTG") -> str:
    return f"{prefix}-{uuid4().hex}"


def initialize_transaction(
    *,
    email: str,
    amount_kobo: int,
    reference: str,
    metadata: Optional[Dict[str, Any]] = None,
    currency: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Calls Paystack transaction/initialize.
    Paystack expects `amount` in KOBO.
    """
    email = (email or "").strip()
    if not email:
        raise ValueError("missing_email")

    if amount_kobo is None or int(amount_kobo) <= 0:
        raise ValueError("invalid_amount_kobo")

    payload: Dict[str, Any] = {
        "email": email,
        "amount": int(amount_kobo),
        "currency": (currency or PAYSTACK_CURRENCY or "NGN"),
        "reference": reference,
        "metadata": metadata or {},
    }

    cb = (PAYSTACK_CALLBACK_URL or "").strip()
    if cb:
        payload["callback_url"] = cb

    try:
        r = requests.post(
            f"{PAYSTACK_BASE}/transaction/initialize",
            headers=_headers(),
            json=payload,
            timeout=25,
        )
    except requests.RequestException as e:
        raise PaystackError(f"paystack_network_error: {e}")

    data = r.json() if r.content else {}

    # Paystack returns {status: bool, message: str, data: {...}}
    if not r.ok or not data.get("status"):
        msg = data.get("message") or f"paystack_init_failed_http_{r.status_code}"
        raise PaystackError(msg)

    return data


def verify_transaction(reference: str) -> Dict[str, Any]:
    reference = (reference or "").strip()
    if not reference:
        raise ValueError("missing_reference")

    try:
        r = requests.get(
            f"{PAYSTACK_BASE}/transaction/verify/{reference}",
            headers=_headers(),
            timeout=25,
        )
    except requests.RequestException as e:
        raise PaystackError(f"paystack_network_error: {e}")

    data = r.json() if r.content else {}
    if not r.ok or not data.get("status"):
        msg = data.get("message") or f"paystack_verify_failed_http_{r.status_code}"
        raise PaystackError(msg)
    return data


def verify_webhook_signature(raw_body: bytes, signature_header: str) -> bool:
    """
    Paystack uses HMAC-SHA512 of raw request body with your secret key.
    Header: x-paystack-signature
    """
    key = (PAYSTACK_SECRET_KEY or "").strip()
    sig = (signature_header or "").strip()
    if not key or not sig or not raw_body:
        return False
    mac = hmac.new(key.encode("utf-8"), msg=raw_body, digestmod=hashlib.sha512).hexdigest()
    return hmac.compare_digest(mac, sig)
