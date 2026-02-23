# app/routes/paystack_webhook.py
from __future__ import annotations

from typing import Any, Dict, Optional
from flask import Blueprint, jsonify, request

from app.core.supabase_client import supabase
from app.services.paystack_service import verify_webhook_signature
from app.services.subscriptions_service import activate_subscription_now

bp = Blueprint("paystack_webhook", __name__)


def _sb():
    return supabase() if callable(supabase) else supabase


def _get(d: Dict[str, Any], path: str) -> Optional[Any]:
    cur: Any = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


@bp.post("/paystack/webhook")
def paystack_webhook():
    raw = request.get_data(cache=False, as_text=False) or b""
    sig = (request.headers.get("x-paystack-signature") or "").strip()

    if not verify_webhook_signature(raw, sig):
        return jsonify({"ok": False, "error": "invalid_signature"}), 401

    payload: Dict[str, Any] = request.get_json(silent=True) or {}
    event_type = (payload.get("event") or "").strip()
    event_id = str(payload.get("id") or "").strip()

    data = payload.get("data") or {}
    reference = str(data.get("reference") or "").strip()
    status = str(data.get("status") or "").strip().lower()
    metadata = data.get("metadata") or {}

    # 1) DEDUPE LOCK: insert event_id (unique)
    if not event_id:
        return jsonify({"ok": True, "needs_reconcile": True, "reason": "missing_event_id"}), 200

    try:
        _sb().table("paystack_events").insert(
            {
                "event_id": event_id,
                "event_type": event_type or "unknown",
                "reference": reference or None,
                "payload": payload,
                "signature": sig or None,
            }
        ).execute()
    except Exception:
        # Duplicate event => already processed
        return jsonify({"ok": True, "deduped": True, "event_id": event_id}), 200

    # 2) UPSERT paystack_transactions by reference (if present)
    if reference:
        amount = data.get("amount")
        currency = data.get("currency")
        paid_at = data.get("paid_at") or data.get("paidAt")

        account_id = (metadata.get("account_id") or "").strip() or None
        plan_code = (metadata.get("plan_code") or "").strip().lower() or None

        try:
            _sb().table("paystack_transactions").upsert(
                {
                    "reference": reference,
                    "status": "success" if status == "success" else (status or "unknown"),
                    "amount": amount,
                    "currency": currency,
                    "paid_at": paid_at,
                    "account_id": account_id,
                    "plan_code": plan_code,
                    "raw": payload,
                },
                on_conflict="reference",
            ).execute()
        except Exception:
            pass

    # 3) Activate only on success
    if event_type not in ("charge.success", "subscription.create", "invoice.payment_succeeded"):
        return jsonify({"ok": True, "ignored": True, "event": event_type}), 200

    if status != "success":
        return jsonify({"ok": True, "ignored": True, "status": status}), 200

    account_id = (metadata.get("account_id") or "").strip()
    plan_code = (metadata.get("plan_code") or "").strip().lower()

    if not account_id or not plan_code:
        return jsonify({"ok": True, "needs_reconcile": True, "reason": "missing_metadata"}), 200

    # 4) Idempotent activate
    result = activate_subscription_now(
        account_id=account_id,
        plan_code=plan_code,
        status="active",
        provider="paystack",
        reference=reference or None,
    )

    return jsonify({"ok": True, "activated": bool(result.get("ok")), "activation": result}), 200
