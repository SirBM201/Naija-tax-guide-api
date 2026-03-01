# app/routes/billing.py
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional, Tuple

from flask import Blueprint, jsonify, request

from app.core.supabase_client import supabase
from app.services.plans_service import get_plan, list_plans
from app.services.web_auth_service import get_account_id_from_request
from app.services.paystack_service import (
    create_reference,
    initialize_transaction,
    verify_transaction,
    verify_webhook_signature,
)

bp = Blueprint("billing", __name__)


def _sb():
    return supabase() if callable(supabase) else supabase


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_json() -> Dict[str, Any]:
    return request.get_json(silent=True) or {}


def _store_paystack_event(*, event_id: Optional[str], event_type: str, reference: Optional[str], payload: Dict[str, Any]) -> None:
    """
    paystack_events schema:
      id bigint pk
      event_id text unique nullable
      event_type text not null
      reference text nullable
      payload jsonb not null
      created_at timestamptz not null
    """
    row = {
        "event_id": event_id,
        "event_type": event_type or "unknown",
        "reference": reference,
        "payload": payload,
        "created_at": _now_iso(),
    }
    try:
        _sb().table("paystack_events").insert(row).execute()
    except Exception:
        # idempotency: event_id is UNIQUE; duplicates should not break webhook
        pass


def _upsert_user_subscription(*, account_id: str, plan_code: str, duration_days: int, provider: str, provider_ref: str) -> Dict[str, Any]:
    """
    user_subscriptions has UNIQUE(account_id). We upsert by:
      - fetch existing by account_id
      - update if exists
      - else insert new
    Columns exist:
      id uuid pk
      account_id uuid not null
      plan_code text not null
      status text not null
      started_at timestamptz not null
      expires_at timestamptz nullable
      is_active boolean not null
      pending_* nullable
      grace_until, trial_until nullable
      provider/provider_ref nullable
      current_period_end nullable
    """
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=int(duration_days))

    now_iso = now.isoformat()
    exp_iso = expires.isoformat()

    # try find existing
    existing = (
        _sb()
        .table("user_subscriptions")
        .select("id,account_id,plan_code,status,is_active,expires_at")
        .eq("account_id", account_id)
        .limit(1)
        .execute()
    )
    rows = getattr(existing, "data", None) or []

    patch = {
        "plan_code": plan_code,
        "status": "active",
        "is_active": True,
        "started_at": now_iso,
        "expires_at": exp_iso,
        "current_period_end": exp_iso,
        "provider": provider,
        "provider_ref": provider_ref,
        # clear pending fields
        "pending_plan_code": None,
        "pending_starts_at": None,
        "updated_at": now_iso,
    }

    if rows:
        sub_id = rows[0]["id"]
        upd = _sb().table("user_subscriptions").update(patch).eq("id", sub_id).execute()
        out = getattr(upd, "data", None) or []
        return out[0] if out else {"id": sub_id, "account_id": account_id, **patch}

    ins = {
        "account_id": account_id,
        "plan_code": plan_code,
        "status": "active",
        "is_active": True,
        "started_at": now_iso,
        "expires_at": exp_iso,
        "current_period_end": exp_iso,
        "provider": provider,
        "provider_ref": provider_ref,
        "created_at": now_iso,
        "updated_at": now_iso,
    }
    created = _sb().table("user_subscriptions").insert(ins).execute()
    out = getattr(created, "data", None) or []
    return out[0] if out else ins


@bp.get("/billing/plans")
def billing_plans():
    active_only = (request.args.get("active_only") or "1").strip() != "0"
    plans = list_plans(active_only=active_only)
    return jsonify({"ok": True, "plans": plans}), 200


@bp.get("/billing/plans/<plan_code>")
def billing_plan(plan_code: str):
    p = get_plan(plan_code)
    if not p:
        return jsonify({"ok": False, "error": "plan_not_found"}), 404
    return jsonify({"ok": True, "plan": p}), 200


@bp.get("/billing/me")
def billing_me():
    account_id, debug = get_account_id_from_request(request)
    if not account_id:
        return jsonify({"ok": False, "error": "unauthorized", "debug": debug}), 401

    # return current subscription snapshot (best effort)
    sub = None
    err = None
    try:
        q = (
            _sb()
            .table("user_subscriptions")
            .select("*")
            .eq("account_id", account_id)
            .limit(1)
            .execute()
        )
        rows = getattr(q, "data", None) or []
        sub = rows[0] if rows else None
    except Exception as e:
        err = repr(e)

    return jsonify({"ok": True, "account_id": account_id, "subscription": sub, "db_warning": err, "debug": debug}), 200


@bp.post("/billing/checkout")
def billing_checkout():
    """
    Start Paystack transaction (requires auth identity because we must bind to account_id).
    Body: { "plan_code": "monthly|quarterly|yearly", "email": "payer@email.com" }
    """
    account_id, debug = get_account_id_from_request(request)
    if not account_id:
        return jsonify({"ok": False, "error": "unauthorized", "debug": debug}), 401

    body = _safe_json()
    plan_code = (body.get("plan_code") or "").strip().lower()
    email = (body.get("email") or "").strip().lower()

    if "@" not in email:
        return jsonify({"ok": False, "error": "invalid_email"}), 400

    plan = get_plan(plan_code)
    if not plan or not plan.get("active", True):
        return jsonify({"ok": False, "error": "plan_not_found"}), 404

    # amount is from DEFAULT_PLANS in code (DB plans has no price column)
    price_ngn = int(plan.get("price") or 0)
    if price_ngn <= 0:
        return jsonify({"ok": False, "error": "plan_price_missing"}), 400

    reference = create_reference("NTG")
    metadata = {
        "product": "naija_tax_guide",
        "plan_code": plan_code,
        "account_id": account_id,   # IMPORTANT: this is accounts.account_id
        "email": email,
    }

    try:
        ps = initialize_transaction(
            email=email,
            amount_kobo=price_ngn * 100,
            reference=reference,
            metadata=metadata,
        )
    except Exception as e:
        return jsonify({"ok": False, "error": "paystack_init_failed", "root_cause": repr(e)}), 400

    data = (ps or {}).get("data") or {}
    return jsonify(
        {
            "ok": True,
            "reference": reference,
            "authorization_url": data.get("authorization_url"),
            "access_code": data.get("access_code"),
            "plan": plan,
            "account_id": account_id,
        }
    ), 200


@bp.get("/billing/verify")
def billing_verify():
    """
    Verify a reference (after Paystack redirect).
    GET /billing/verify?reference=...
    """
    reference = (request.args.get("reference") or "").strip()
    if not reference:
        return jsonify({"ok": False, "error": "missing_reference"}), 400

    try:
        ps = verify_transaction(reference)
    except Exception as e:
        return jsonify({"ok": False, "error": "paystack_verify_failed", "root_cause": repr(e)}), 400

    tx = (ps or {}).get("data") or {}
    status = (tx.get("status") or "").strip().lower()
    tx_id = str(tx.get("id") or "") or None
    metadata = tx.get("metadata") or {}

    plan_code = (metadata.get("plan_code") or "").strip().lower()
    account_id = (metadata.get("account_id") or "").strip()

    # store verify payload in paystack_events (best effort)
    _store_paystack_event(event_id=tx_id, event_type="verify", reference=reference, payload=ps)

    if status != "success":
        return jsonify({"ok": True, "paid": False, "status": status, "reference": reference, "data": tx}), 200

    if not plan_code or not account_id:
        return jsonify({"ok": False, "error": "missing_metadata", "metadata": metadata, "reference": reference}), 400

    plan = get_plan(plan_code)
    if not plan:
        return jsonify({"ok": False, "error": "plan_not_found", "plan_code": plan_code}), 404

    sub = _upsert_user_subscription(
        account_id=account_id,
        plan_code=plan_code,
        duration_days=int(plan["duration_days"]),
        provider="paystack",
        provider_ref=reference,
    )

    return jsonify({"ok": True, "paid": True, "reference": reference, "subscription": sub, "plan": plan}), 200


@bp.post("/billing/webhook")
def billing_webhook():
    """
    Paystack webhook:
    - validates signature (x-paystack-signature)
    - stores event in paystack_events
    - on charge.success -> activates subscription
    """
    raw_body = request.get_data(cache=False) or b""
    sig = (request.headers.get("x-paystack-signature") or "").strip()

    if not verify_webhook_signature(raw_body, sig):
        return jsonify({"ok": False, "error": "invalid_signature"}), 401

    payload = request.get_json(silent=True) or {}
    event_type = (payload.get("event") or "").strip().lower()
    data = payload.get("data") or {}
    reference = (data.get("reference") or "").strip() or None
    tx_id = str(data.get("id") or "") or None

    _store_paystack_event(event_id=tx_id, event_type=event_type or "unknown", reference=reference, payload=payload)

    if event_type == "charge.success":
        metadata = data.get("metadata") or {}
        plan_code = (metadata.get("plan_code") or "").strip().lower()
        account_id = (metadata.get("account_id") or "").strip()

        if plan_code and account_id and reference:
            plan = get_plan(plan_code)
            if plan:
                _upsert_user_subscription(
                    account_id=account_id,
                    plan_code=plan_code,
                    duration_days=int(plan["duration_days"]),
                    provider="paystack",
                    provider_ref=reference,
                )

    return jsonify({"ok": True}), 200
