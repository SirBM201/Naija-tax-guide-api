# app/routes/paystack.py
from __future__ import annotations

import hmac
import hashlib
import os
from typing import Any, Dict, Optional

from flask import Blueprint, jsonify, request

from app.services.subscriptions_service import activate_subscription_now
from app.core.security import require_admin_key

# ---------------------------------------------------------
# Blueprint
# ---------------------------------------------------------
# IMPORTANT:
# Your boot log showed the loader expects: app.routes.paystack:paystack_bp
# So we MUST export paystack_bp.
paystack_bp = Blueprint("paystack", __name__)

# Also export bp as an alias in case some code expects bp
bp = paystack_bp


# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------
def _get_secret() -> str:
    # Use one env var name consistently (this is what you used in PS)
    return (os.getenv("PAYSTACK_WEBHOOK_SECRET") or "").strip()


def _safe_json() -> Dict[str, Any]:
    return request.get_json(silent=True) or {}


def _raw_body_bytes() -> bytes:
    # Paystack signature must be computed over the raw request body
    return request.get_data(cache=False, as_text=False) or b""


def _compute_signature_hex(raw: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), raw, hashlib.sha512).hexdigest()


def _constant_time_equal(a: str, b: str) -> bool:
    # Avoid timing attacks
    try:
        return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
    except Exception:
        return False


def _verify_paystack_signature() -> Optional[Dict[str, Any]]:
    """
    Returns an error dict if invalid; None if OK.
    """
    secret = _get_secret()
    if not secret:
        return {"ok": False, "error": "missing_webhook_secret"}

    got = (request.headers.get("x-paystack-signature") or "").strip().lower()
    if not got:
        return {"ok": False, "error": "missing_signature_header"}

    raw = _raw_body_bytes()
    expected = _compute_signature_hex(raw, secret).lower()

    if not _constant_time_equal(expected, got):
        return {"ok": False, "error": "invalid_signature"}

    return None


# ---------------------------------------------------------
# Routes
# ---------------------------------------------------------
@paystack_bp.post("/webhooks/paystack")
def paystack_webhook():
    """
    Paystack Webhook endpoint.
    Expected header: x-paystack-signature = HMAC-SHA512(raw_body, PAYSTACK_WEBHOOK_SECRET)
    """
    sig_err = _verify_paystack_signature()
    if sig_err is not None:
        return jsonify(sig_err), 401

    payload = _safe_json()

    event = (payload.get("event") or "").strip()
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        data = {}

    reference = (data.get("reference") or "").strip()

    metadata = data.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}

    account_id = (metadata.get("account_id") or "").strip()
    plan_code = (metadata.get("plan_code") or "").strip() or "monthly"
    upgrade_mode = (metadata.get("upgrade_mode") or "").strip() or "now"

    # We only act on charge.success (typical for subscription activation)
    if event != "charge.success":
        return jsonify({
            "ok": True,
            "processed": False,
            "event": event,
            "reference": reference,
            "reason": "ignored_event"
        }), 200

    if not account_id:
        return jsonify({
            "ok": False,
            "processed": False,
            "event": event,
            "reference": reference,
            "error": "missing_account_id_in_metadata"
        }), 400

    # Activate subscription (your existing service)
    out = activate_subscription_now(
        account_id=account_id,
        plan_code=plan_code,
        # days is optional; for webhook mode you may map plan_code -> days in the service
        days=None,
        reference=reference,
        upgrade_mode=upgrade_mode,
        source="paystack_webhook",
    )

    return jsonify({
        "ok": bool(out.get("ok")),
        "processed": bool(out.get("ok")),
        "event": event,
        "reference": reference,
        "account_id": account_id,
        "plan_code": plan_code,
        "upgrade_mode": upgrade_mode,
        "activation": out,
    }), (200 if out.get("ok") else 400)


# Optional debug endpoint (admin protected)
@paystack_bp.post("/_debug/paystack/signature_check")
def debug_signature_check():
    """
    Admin-only endpoint to verify the server can compute signature for the received body.
    This helps you debug signature mismatches quickly.
    """
    guard = require_admin_key()
    if guard is not None:
        return guard

    secret = _get_secret()
    raw = _raw_body_bytes()
    got = (request.headers.get("x-paystack-signature") or "").strip().lower()
    expected = _compute_signature_hex(raw, secret).lower() if secret else ""

    return jsonify({
        "ok": True,
        "has_secret": bool(secret),
        "raw_len": len(raw),
        "got_present": bool(got),
        "expected_prefix": expected[:16],
        "got_prefix": got[:16],
        "match": _constant_time_equal(expected, got) if secret and got else False,
    }), 200
