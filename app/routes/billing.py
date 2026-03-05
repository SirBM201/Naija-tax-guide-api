# app/routes/billing.py
from __future__ import annotations

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
from app.services.credits_service import init_credits_for_plan

bp = Blueprint("billing", __name__)


def _sb():
    return supabase() if callable(supabase) else supabase


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _safe_json() -> Dict[str, Any]:
    return request.get_json(silent=True) or {}


def _clip(v: Any, n: int = 400) -> str:
    s = str(v or "")
    return s if len(s) <= n else s[:n] + "...<truncated>"


def _looks_like_email(v: str) -> bool:
    v = (v or "").strip()
    return ("@" in v) and ("." in v.split("@")[-1])


def _store_paystack_event(
    *,
    event_id: Optional[str],
    event_type: str,
    reference: Optional[str],
    payload: Dict[str, Any],
) -> None:
    """
    Table expected (recommended):
      paystack_events:
        id bigint pk
        event_id text unique nullable
        event_type text not null
        reference text nullable
        payload jsonb not null
        created_at timestamptz not null
    Best-effort only.
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
        pass


def _fetch_payer_email(account_id: str) -> Tuple[Optional[str], Dict[str, Any]]:
    """
    Determine payer email from accounts row.
    Priority:
      1) accounts.email (if present)
      2) accounts.provider_user_id if it looks like email (web login uses email as provider_user_id)
    """
    try:
        q = (
            _sb()
            .table("accounts")
            .select("id,email,provider,provider_user_id,display_name")
            .eq("id", account_id)
            .limit(1)
            .execute()
        )
        rows = getattr(q, "data", None) or []
        if not rows:
            return None, {"error": "account_not_found", "details": {"account_id": account_id}}

        row = rows[0] or {}
        email = (row.get("email") or "").strip().lower()
        if _looks_like_email(email):
            return email, {"source": "accounts.email", "account": {"id": row.get("id"), "provider": row.get("provider")}}

        puid = (row.get("provider_user_id") or "").strip().lower()
        if _looks_like_email(puid):
            return puid, {
                "source": "accounts.provider_user_id",
                "account": {"id": row.get("id"), "provider": row.get("provider")},
            }

        return None, {
            "error": "missing_payer_email",
            "fix": "Set accounts.email for this user OR ensure provider_user_id is the email for web provider.",
            "account_row": {"id": row.get("id"), "email": row.get("email"), "provider_user_id": row.get("provider_user_id")},
        }

    except Exception as e:
        return None, {
            "error": "payer_email_lookup_failed",
            "root_cause": f"{type(e).__name__}: {_clip(e)}",
            "fix": "Check Supabase connectivity/RLS for accounts table and ensure service role key is used.",
            "details": {"account_id": account_id},
        }


def _upsert_user_subscription(
    *,
    account_id: str,
    plan_code: str,
    duration_days: int,
    provider: str,
    provider_ref: str,
) -> Dict[str, Any]:
    now = _now()
    expires = now + timedelta(days=int(duration_days))
    now_iso = now.isoformat()
    exp_iso = expires.isoformat()

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


# -------------------- ROUTES --------------------

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

    sub = None
    err = None
    try:
        q = _sb().table("user_subscriptions").select("*").eq("account_id", account_id).limit(1).execute()
        rows = getattr(q, "data", None) or []
        sub = rows[0] if rows else None
    except Exception as e:
        err = f"{type(e).__name__}: {_clip(e)}"

    return jsonify({"ok": True, "account_id": account_id, "subscription": sub, "db_warning": err, "debug": debug}), 200


@bp.post("/billing/checkout")
def billing_checkout():
    """
    Start Paystack transaction (requires web auth).
    Body: { "plan_code": "monthly|quarterly|yearly" }
    Email is fetched from accounts table (best practice).
    """
    account_id, debug = get_account_id_from_request(request)
    if not account_id:
        return jsonify({"ok": False, "error": "unauthorized", "debug": debug}), 401

    body = _safe_json()
    plan_code = (body.get("plan_code") or "").strip().lower()
    if not plan_code:
        return jsonify({"ok": False, "error": "plan_code_required"}), 400

    plan = get_plan(plan_code)
    if not plan or not plan.get("active", True):
        return jsonify({"ok": False, "error": "plan_not_found"}), 404

    price_ngn = int(plan.get("price") or 0)
    if price_ngn <= 0:
        return jsonify({"ok": False, "error": "plan_price_missing", "plan": plan}), 400

    payer_email, email_dbg = _fetch_payer_email(account_id)
    if not payer_email:
        return jsonify({"ok": False, **email_dbg, "debug": debug}), 400

    reference = create_reference("NTG")
    metadata = {
        "product": "naija_tax_guide",
        "plan_code": plan_code,
        "account_id": account_id,  # IMPORTANT
        "email": payer_email,
    }

    try:
        ps = initialize_transaction(
            email=payer_email,
            amount_kobo=price_ngn * 100,
            reference=reference,
            metadata=metadata,
        )
    except Exception as e:
        return jsonify(
            {
                "ok": False,
                "error": "paystack_init_failed",
                "root_cause": f"{type(e).__name__}: {_clip(e)}",
                "details": {"reference": reference, "plan_code": plan_code, "amount_kobo": price_ngn * 100},
            }
        ), 400

    data = (ps or {}).get("data") or {}
    return jsonify(
        {
            "ok": True,
            "reference": reference,
            "authorization_url": data.get("authorization_url"),
            "access_code": data.get("access_code"),
            "plan": plan,
            "account_id": account_id,
            "payer_email": payer_email,
            "email_debug": email_dbg,
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
        return jsonify({"ok": False, "error": "paystack_verify_failed", "root_cause": f"{type(e).__name__}: {_clip(e)}"}), 400

    tx = (ps or {}).get("data") or {}
    status = (tx.get("status") or "").strip().lower()
    tx_id = str(tx.get("id") or "") or None
    metadata = tx.get("metadata") or {}

    plan_code = (metadata.get("plan_code") or "").strip().lower()
    account_id = (metadata.get("account_id") or "").strip()

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

    # ✅ Recommended: initialize credits immediately based on plan
    credits = init_credits_for_plan(account_id, plan_code)

    return jsonify({"ok": True, "paid": True, "reference": reference, "subscription": sub, "plan": plan, "credits": credits}), 200


@bp.post("/billing/webhook")
def billing_webhook():
    """
    Paystack webhook:
    - validates signature (x-paystack-signature)
    - stores event in paystack_events
    - on charge.success -> activates subscription + initializes credits
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
                init_credits_for_plan(account_id, plan_code)

    return jsonify({"ok": True}), 200
