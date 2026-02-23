from __future__ import annotations

import hmac
import hashlib
import json
import os
from typing import Any, Dict, Optional, Tuple
from uuid import uuid4

import requests

from app.core.config import PAYSTACK_SECRET_KEY, PAYSTACK_CURRENCY, PAYSTACK_CALLBACK_URL

PAYSTACK_BASE = "https://api.paystack.co"


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or default).strip()


def _truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "y", "on"}


PAYSTACK_DEBUG = _truthy(_env("PAYSTACK_DEBUG", "0"))


def _clip(s: str, n: int = 500) -> str:
    s = (s or "")
    return s if len(s) <= n else s[:n] + "…"


def _headers() -> Dict[str, str]:
    # IMPORTANT: do NOT raise here; let callers return JSON errors.
    if not PAYSTACK_SECRET_KEY:
        return {}
    return {
        "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def paystack_is_configured() -> bool:
    return bool(PAYSTACK_SECRET_KEY)


def create_reference(prefix: str = "NTG") -> str:
    return f"{prefix}-{uuid4().hex}"


def initialize_transaction(
    *,
    email: str,
    amount_kobo: int,
    reference: Optional[str] = None,
    currency: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    callback_url: Optional[str] = None,
) -> Tuple[bool, Dict[str, Any]]:
    """
    Initialize Paystack transaction.

    - Paystack expects `amount` in KOBO (integer).
    - Returns (ok, payload) where payload is safe JSON.
    """
    if not paystack_is_configured():
        return False, {
            "ok": False,
            "error": "paystack_not_configured",
            "message": "PAYSTACK_SECRET_KEY not configured on server.",
        }

    email = (email or "").strip().lower()
    if not email or "@" not in email:
        return False, {"ok": False, "error": "invalid_email"}

    try:
        amount_kobo_int = int(amount_kobo)
    except Exception:
        return False, {"ok": False, "error": "invalid_amount", "message": "amount_kobo must be an integer (kobo)."}

    if amount_kobo_int <= 0:
        return False, {"ok": False, "error": "invalid_amount", "message": "amount_kobo must be > 0."}

    ref = (reference or "").strip() or create_reference("NTG")
    cur = (currency or PAYSTACK_CURRENCY or "NGN").strip().upper() or "NGN"

    payload: Dict[str, Any] = {
        "email": email,
        "amount": amount_kobo_int,
        "currency": cur,
        "reference": ref,
        "metadata": metadata or {},
    }

    cb = (callback_url or PAYSTACK_CALLBACK_URL or "").strip()
    if cb:
        payload["callback_url"] = cb

    url = f"{PAYSTACK_BASE}/transaction/initialize"

    try:
        r = requests.post(url, headers=_headers(), data=json.dumps(payload), timeout=25)
    except Exception as e:
        out = {"ok": False, "error": "paystack_network_error", "message": "Failed to reach Paystack."}
        if PAYSTACK_DEBUG:
            out["debug"] = {"root_cause": repr(e)[:240], "url": url}
        return False, out

    # Paystack should return JSON — but guard hard.
    try:
        data = r.json() if r.content else {}
    except Exception:
        out = {"ok": False, "error": "paystack_bad_response", "message": "Paystack returned non-JSON response."}
        if PAYSTACK_DEBUG:
            out["debug"] = {"status": r.status_code, "body": _clip(r.text, 900)}
        return False, out

    if not r.ok or not data.get("status"):
        out = {"ok": False, "error": "paystack_init_failed", "message": data.get("message") or "paystack_init_failed"}
        if PAYSTACK_DEBUG:
            out["debug"] = {"status": r.status_code, "paystack": data}
        return False, out

    d = data.get("data") or {}
    return True, {
        "ok": True,
        "authorization_url": d.get("authorization_url"),
        "access_code": d.get("access_code"),
        "reference": d.get("reference") or ref,
        "currency": cur,
        "amount_kobo": amount_kobo_int,
        "raw": data if PAYSTACK_DEBUG else None,
    }


def verify_transaction(reference: str) -> Tuple[bool, Dict[str, Any]]:
    """
    Verify Paystack transaction.
    Returns (ok, payload) safe JSON.
    """
    if not paystack_is_configured():
        return False, {
            "ok": False,
            "error": "paystack_not_configured",
            "message": "PAYSTACK_SECRET_KEY not configured on server.",
        }

    ref = (reference or "").strip()
    if not ref:
        return False, {"ok": False, "error": "missing_reference"}

    url = f"{PAYSTACK_BASE}/transaction/verify/{ref}"

    try:
        r = requests.get(url, headers=_headers(), timeout=25)
    except Exception as e:
        out = {"ok": False, "error": "paystack_network_error", "message": "Failed to reach Paystack."}
        if PAYSTACK_DEBUG:
            out["debug"] = {"root_cause": repr(e)[:240], "url": url}
        return False, out

    try:
        data = r.json() if r.content else {}
    except Exception:
        out = {"ok": False, "error": "paystack_bad_response", "message": "Paystack returned non-JSON response."}
        if PAYSTACK_DEBUG:
            out["debug"] = {"status": r.status_code, "body": _clip(r.text, 900)}
        return False, out

    if not r.ok or not data.get("status"):
        out = {"ok": False, "error": "paystack_verify_failed", "message": data.get("message") or "paystack_verify_failed"}
        if PAYSTACK_DEBUG:
            out["debug"] = {"status": r.status_code, "paystack": data}
        return False, out

    d = data.get("data") or {}
    return True, {
        "ok": True,
        "reference": d.get("reference"),
        "status": (d.get("status") or "").lower(),
        "amount_kobo": d.get("amount"),
        "currency": d.get("currency"),
        "paid_at": d.get("paid_at"),
        "channel": d.get("channel"),
        "customer": d.get("customer"),
        "metadata": d.get("metadata") or {},
        "raw": d if PAYSTACK_DEBUG else None,
    }


def verify_webhook_signature(raw_body: bytes, signature_header: str) -> bool:
    """
    Paystack webhook signature uses HMAC SHA512 of raw request body with SECRET KEY.
    """
    if not PAYSTACK_SECRET_KEY:
        return False
    if not signature_header:
        return False
    mac = hmac.new(PAYSTACK_SECRET_KEY.encode("utf-8"), msg=raw_body, digestmod=hashlib.sha512).hexdigest()
    return hmac.compare_digest(mac, signature_header)


def paystack_debug_snapshot() -> Dict[str, Any]:
    """
    SAFE debug snapshot (never returns secret value).
    """
    return {
        "paystack_configured": bool(PAYSTACK_SECRET_KEY),
        "PAYSTACK_SECRET_KEY": "set" if PAYSTACK_SECRET_KEY else None,
        "PAYSTACK_CURRENCY": (PAYSTACK_CURRENCY or "NGN"),
        "PAYSTACK_CALLBACK_URL_set": bool((PAYSTACK_CALLBACK_URL or "").strip()),
        "PAYSTACK_DEBUG": bool(PAYSTACK_DEBUG),
        "PAYSTACK_BASE": PAYSTACK_BASE,
    }
