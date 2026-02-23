# app/services/paystack_service.py
from __future__ import annotations

import hmac
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, Optional
from uuid import uuid4

import requests

from app.core.config import (
    PAYSTACK_SECRET_KEY,
    PAYSTACK_CURRENCY,
    PAYSTACK_CALLBACK_URL,
)

PAYSTACK_BASE = "https://api.paystack.co"


@dataclass
class PaystackHTTPError(RuntimeError):
    status_code: int
    message: str
    raw: Any = None

    def __str__(self) -> str:
        return f"PaystackHTTPError({self.status_code}): {self.message}"


def _headers() -> Dict[str, str]:
    if not PAYSTACK_SECRET_KEY:
        raise RuntimeError("PAYSTACK_SECRET_KEY not configured")
    return {
        "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json",
    }


def create_reference(prefix: str = "NTG") -> str:
    return f"{prefix}-{uuid4().hex}"


def _paystack_request(method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    url = f"{PAYSTACK_BASE}{path}"
    try:
        r = requests.request(
            method=method.upper(),
            url=url,
            headers=_headers(),
            data=json.dumps(payload) if payload is not None else None,
            timeout=25,
        )
    except Exception as e:
        raise RuntimeError(f"paystack_request_failed: {e}")

    data: Dict[str, Any] = {}
    if r.content:
        try:
            data = r.json()
        except Exception:
            # Paystack should return JSON; keep raw for debug
            raise PaystackHTTPError(status_code=r.status_code, message="non_json_response", raw=r.text)

    # Paystack typically returns {"status": true/false, "message": "...", "data": {...}}
    if not r.ok or not data.get("status"):
        msg = data.get("message") or "paystack_failed"
        raise PaystackHTTPError(status_code=r.status_code, message=msg, raw=data or r.text)

    return data


def initialize_transaction(
    *,
    email: str,
    amount_naira: int,
    reference: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Paystack expects amount in KOBO. We accept NAIRA and multiply by 100.
    """
    if not email:
        raise ValueError("missing_email")
    if not reference:
        raise ValueError("missing_reference")

    payload: Dict[str, Any] = {
        "email": email,
        "amount": int(amount_naira) * 100,
        "currency": PAYSTACK_CURRENCY or "NGN",
        "reference": reference,
        "metadata": metadata or {},
    }

    if PAYSTACK_CALLBACK_URL:
        payload["callback_url"] = PAYSTACK_CALLBACK_URL

    return _paystack_request("POST", "/transaction/initialize", payload=payload)


def verify_transaction(reference: str) -> Dict[str, Any]:
    reference = (reference or "").strip()
    if not reference:
        raise ValueError("missing_reference")
    return _paystack_request("GET", f"/transaction/verify/{reference}", payload=None)


def verify_webhook_signature(raw_body: bytes, signature_header: str) -> bool:
    if not PAYSTACK_SECRET_KEY:
        return False
    if not signature_header:
        return False
    mac = hmac.new(
        PAYSTACK_SECRET_KEY.encode("utf-8"),
        msg=raw_body,
        digestmod=hashlib.sha512,
    ).hexdigest()
    return hmac.compare_digest(mac, signature_header)
