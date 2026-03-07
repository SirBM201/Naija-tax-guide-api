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


def _safe_dt(v: Any) -> Optional[datetime]:
    try:
        if not v:
            return None
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _fail(*, error: str, root_cause: Any = None, extra: Dict[str, Any] | None = None, status: int = 400):
    out: Dict[str, Any] = {"ok": False, "error": error}
    if root_cause is not None:
        out["root_cause"] = root_cause
    if extra:
        out.update(extra)
    return jsonify(out), status


def _store_paystack_event(
    *,
    event_id: Optional[str],
    event_type: str,
    reference: Optional[str],
    payload: Dict[str, Any],
) -> None:
    """
    Best-effort audit log.
    Expected table:
      paystack_events(
        id bigint pk,
        event_id text unique null,
        event_type text not null,
        reference text null,
        payload jsonb not null,
        created_at timestamptz not null
      )
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


def _get_account_row(account_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """
    account_id here is canonical accounts.account_id from web auth.
    """
    account_id = (account_id or "").strip()
    if not account_id:
        return None, {"error": "account_id_required", "root_cause": "missing_account_id"}

    try:
        q = (
            _sb()
            .table("accounts")
            .select("id,account_id,email,provider,provider_user_id,display_name,created_at,updated_at")
            .eq("account_id", account_id)
            .limit(1)
            .execute()
        )
        rows = getattr(q, "data", None) or []
        if rows:
            return rows[0], None
    except Exception as e:
        return None, {
            "error": "account_lookup_failed",
            "root_cause": f"lookup by account_id failed: {type(e).__name__}: {_clip(e)}",
        }

    try:
        q = (
            _sb()
            .table("accounts")
            .select("id,account_id,email,provider,provider_user_id,display_name,created_at,updated_at")
            .eq("id", account_id)
            .limit(1)
            .execute()
        )
        rows = getattr(q, "data", None) or []
        if rows:
            return rows[0], None
    except Exception as e:
        return None, {
            "error": "account_lookup_failed",
            "root_cause": f"lookup by id failed: {type(e).__name__}: {_clip(e)}",
        }

    return None, {"error": "account_not_found", "root_cause": "no accounts row matched provided account_id"}


def _resolve_checkout_email(account_id: str) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """
    Resolve checkout email automatically from authenticated account.
    Priority:
      1. accounts.email
      2. accounts.provider_user_id when provider=web and looks like email
    """
    row, err = _get_account_row(account_id)
    if err:
        return None, err

    email = (row.get("email") or "").strip().lower()
    if "@" in email:
        return email, None

    provider = (row.get("provider") or "").strip().lower()
    provider_user_id = (row.get("provider_user_id") or "").strip().lower()
    if provider == "web" and "@" in provider_user_id:
        return provider_user_id, None

    return None, {
        "error": "checkout_email_missing",
        "root_cause": "No valid email found on accounts.email or provider_user_id",
        "details": {
            "account_id": account_id,
            "provider": provider,
            "provider_user_id": provider_user_id,
            "email": email,
        },
        "fix": "Ensure accounts.email is populated for this authenticated account.",
    }


def _get_subscription_row(account_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    account_id = (account_id or "").strip()
    if not account_id:
        return None, {"error": "account_id_required", "root_cause": "missing_account_id"}

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
        return (rows[0] if rows else None), None
    except Exception as e:
        return None, {
            "error": "subscription_lookup_failed",
            "root_cause": f"{type(e).__name__}: {_clip(e)}",
        }


def _same_active_plan_guard(account_id: str, requested_plan_code: str) -> Optional[Tuple[Any, int]]:
    """
    Block checkout if the user already has the same active plan and it hasn't expired.
    """
    sub, err = _get_subscription_row(account_id)
    if err:
        # Don't block checkout on lookup issues; expose warning elsewhere.
        return None

    if not sub:
        return None

    current_plan_code = (sub.get("plan_code") or "").strip().lower()
    requested_plan_code = (requested_plan_code or "").strip().lower()
    status = (sub.get("status") or "").strip().lower()
    is_active = bool(sub.get("is_active"))
    expires_at = _safe_dt(sub.get("expires_at"))

    same_plan = current_plan_code and current_plan_code == requested_plan_code
    still_valid = expires_at is None or _now() < expires_at

    if same_plan and is_active and status == "active" and still_valid:
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "same_active_plan_exists",
                    "root_cause": "requested_plan_matches_current_active_plan",
                    "fix": "Use billing page to review the current subscription instead of purchasing the same active plan again.",
                    "details": {
                        "account_id": account_id,
                        "current_subscription": {
                            "id": sub.get("id"),
                            "plan_code": sub.get("plan_code"),
                            "status": sub.get("status"),
                            "is_active": sub.get("is_active"),
                            "expires_at": sub.get("expires_at"),
                            "provider": sub.get("provider"),
                            "provider_ref": sub.get("provider_ref"),
                        },
                    },
                }
            ),
            409,
        )

    return None


def _upsert_user_subscription(
    *,
    account_id: str,
    plan_code: str,
    duration_days: int,
    provider: str,
    provider_ref: str,
) -> Dict[str, Any]:
    """
    user_subscriptions uses canonical account_id (accounts.account_id).
    """
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
    db_warning = None
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
        db_warning = repr(e)

    checkout_email, email_err = _resolve_checkout_email(account_id)

    return jsonify(
        {
            "ok": True,
            "account_id": account_id,
            "subscription": sub,
            "checkout_email": checkout_email,
            "checkout_email_error": email_err,
            "db_warning": db_warning,
            "debug": debug,
        }
    ), 200


@bp.post("/billing/checkout")
def billing_checkout():
    """
    Start Paystack transaction.
    Email is resolved automatically from the authenticated account.
    Body:
      { "plan_code": "monthly|quarterly|yearly" }
    """
    account_id, debug = get_account_id_from_request(request)
    if not account_id:
        return jsonify({"ok": False, "error": "unauthorized", "debug": debug}), 401

    body = _safe_json()
    plan_code = (body.get("plan_code") or "").strip().lower()

    if not plan_code:
        return _fail(error="plan_code_required", status=400)

    plan = get_plan(plan_code)
    if not plan or not plan.get("active", True):
        return _fail(error="plan_not_found", status=404)

    same_plan_block = _same_active_plan_guard(account_id, plan_code)
    if same_plan_block is not None:
        return same_plan_block

    price_ngn = int(plan.get("price") or 0)
    if price_ngn <= 0:
        return _fail(error="plan_price_missing", status=400)

    email, email_err = _resolve_checkout_email(account_id)
    if email_err or not email:
        return _fail(
            error="checkout_email_missing",
            root_cause=(email_err or {}).get("root_cause"),
            extra={
                "details": (email_err or {}).get("details"),
                "fix": (email_err or {}).get("fix"),
                "account_id": account_id,
            },
            status=400,
        )

    reference = create_reference("NTG")
    metadata = {
        "product": "naija_tax_guide",
        "plan_code": plan_code,
        "account_id": account_id,
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
        return _fail(
            error="paystack_init_failed",
            root_cause=repr(e),
            extra={"account_id": account_id, "email": email, "plan_code": plan_code},
            status=400,
        )

    data = (ps or {}).get("data") or {}
    return jsonify(
        {
            "ok": True,
            "reference": reference,
            "authorization_url": data.get("authorization_url"),
            "access_code": data.get("access_code"),
            "plan": plan,
            "account_id": account_id,
            "email": email,
        }
    ), 200


@bp.get("/billing/verify")
def billing_verify():
    """
    Verify a reference after Paystack redirect.
    GET /billing/verify?reference=...
    """
    reference = (request.args.get("reference") or "").strip()
    if not reference:
        return _fail(error="missing_reference", status=400)

    try:
        ps = verify_transaction(reference)
    except Exception as e:
        return _fail(error="paystack_verify_failed", root_cause=repr(e), status=400)

    tx = (ps or {}).get("data") or {}
    status_text = (tx.get("status") or "").strip().lower()
    tx_id = str(tx.get("id") or "") or None
    metadata = tx.get("metadata") or {}

    plan_code = (metadata.get("plan_code") or "").strip().lower()
    account_id = (metadata.get("account_id") or "").strip()

    _store_paystack_event(event_id=tx_id, event_type="verify", reference=reference, payload=ps)

    if status_text != "success":
        return jsonify(
            {
                "ok": True,
                "paid": False,
                "status": status_text,
                "reference": reference,
                "data": tx,
            }
        ), 200

    if not plan_code or not account_id:
        return _fail(
            error="missing_metadata",
            extra={"metadata": metadata, "reference": reference},
            status=400,
        )

    plan = get_plan(plan_code)
    if not plan:
        return _fail(error="plan_not_found", extra={"plan_code": plan_code}, status=404)

    sub = _upsert_user_subscription(
        account_id=account_id,
        plan_code=plan_code,
        duration_days=int(plan["duration_days"]),
        provider="paystack",
        provider_ref=reference,
    )

    return jsonify(
        {
            "ok": True,
            "paid": True,
            "reference": reference,
            "subscription": sub,
            "plan": plan,
        }
    ), 200


@bp.post("/billing/webhook")
def billing_webhook():
    """
    Paystack webhook:
    - validates signature
    - stores event
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

    _store_paystack_event(
        event_id=tx_id,
        event_type=event_type or "unknown",
        reference=reference,
        payload=payload,
    )

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
