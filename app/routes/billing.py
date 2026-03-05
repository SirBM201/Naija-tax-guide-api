# app/routes/billing.py
from __future__ import annotations

import os
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


# -------------------- Helpers --------------------

def _sb():
    return supabase() if callable(supabase) else supabase


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _safe_json() -> Dict[str, Any]:
    return request.get_json(silent=True) or {}


def _truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return _truthy(v)


def _fail(
    *,
    status_code: int,
    error: str,
    stage: str,
    hint: str = "",
    root_cause: str = "",
    debug: Any = None,
    extra: Optional[Dict[str, Any]] = None,
):
    """
    Standard error response with failure exposure.
    - stage: where it failed
    - hint: what to check
    - root_cause: repr(exception) when safe
    """
    payload: Dict[str, Any] = {
        "ok": False,
        "error": error,
        "stage": stage,
    }
    if hint:
        payload["hint"] = hint
    if root_cause:
        payload["root_cause"] = root_cause
    if debug is not None:
        payload["debug"] = debug
    if extra:
        payload.update(extra)
    return jsonify(payload), status_code


def _store_paystack_event(
    *,
    event_id: Optional[str],
    event_type: str,
    reference: Optional[str],
    payload: Dict[str, Any],
) -> None:
    """
    Best-effort audit logging.
    Recommended table:
      paystack_events:
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
        # idempotency / table missing must not break payments
        pass


def _get_account_email(account_id: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Returns (email, warning).
    warning is used for failure exposure without crashing.

    Note: If accounts.email column doesn't exist yet, Supabase will throw.
    We capture that and return warning to guide the fix.
    """
    try:
        q = (
            _sb()
            .table("accounts")
            .select("email")
            .eq("id", account_id)
            .limit(1)
            .execute()
        )
        rows = getattr(q, "data", None) or []
        if not rows:
            return None, "accounts_row_not_found"
        email = (rows[0].get("email") or "").strip().lower()
        return (email or None), None
    except Exception as e:
        # likely: column "email" does not exist
        return None, f"accounts_email_fetch_failed: {repr(e)}"


def _upsert_user_subscription(
    *,
    account_id: str,
    plan_code: str,
    duration_days: int,
    provider: str,
    provider_ref: str,
) -> Dict[str, Any]:
    """
    user_subscriptions unique by account_id (one current subscription per account).
    This function will update existing row if found else insert new row.
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

    return jsonify(
        {
            "ok": True,
            "account_id": account_id,
            "subscription": sub,
            "db_warning": err,
            "debug": debug,
        }
    ), 200


@bp.post("/billing/checkout")
def billing_checkout():
    """
    Start Paystack transaction (secure, standard).
    Body: { "plan_code": "monthly|quarterly|yearly" }

    Email handling (standard):
      - Fetch email from accounts.email (canonical)
      - If you haven't added accounts.email yet, this will return a clear error
      - Optional fallback: allow client email ONLY if env flag enabled
        ALLOW_CHECKOUT_EMAIL_OVERRIDE=true
    """
    account_id, debug = get_account_id_from_request(request)
    if not account_id:
        return _fail(
            status_code=401,
            error="unauthorized",
            stage="auth",
            hint="Missing or invalid bearer token",
            debug=debug,
        )

    body = _safe_json()
    plan_code = (body.get("plan_code") or "").strip().lower()
    if not plan_code:
        return _fail(
            status_code=400,
            error="missing_plan_code",
            stage="validate_input",
            hint='Send JSON: {"plan_code":"monthly"}',
            debug=debug,
        )

    plan = get_plan(plan_code)
    if not plan or not plan.get("active", True):
        return _fail(
            status_code=404,
            error="plan_not_found",
            stage="validate_plan",
            hint="Check /billing/plans and ensure plan_code exists and active",
            extra={"plan_code": plan_code},
            debug=debug,
        )

    price_ngn = int(plan.get("price") or 0)
    if price_ngn <= 0:
        return _fail(
            status_code=400,
            error="plan_price_missing",
            stage="validate_plan",
            hint="Plan price must be > 0 in plans_service",
            extra={"plan_code": plan_code},
            debug=debug,
        )

    # ---- Canonical email fetch ----
    email, warn = _get_account_email(account_id)

    # Optional controlled fallback (use ONLY while migrating schema)
    allow_override = _env_bool("ALLOW_CHECKOUT_EMAIL_OVERRIDE", False)
    if (not email) and allow_override:
        override = (body.get("email") or "").strip().lower()
        if "@" in override:
            email = override

    if not email or "@" not in email:
        return _fail(
            status_code=400,
            error="account_email_missing",
            stage="payer_email",
            hint=(
                "Add accounts.email column and store user's email during login/signup. "
                "OR temporarily set ALLOW_CHECKOUT_EMAIL_OVERRIDE=true and send {email} from frontend."
            ),
            extra={"account_id": account_id, "db_warning": warn},
            debug=debug,
        )

    reference = create_reference("NTG")
    metadata = {
        "product": "naija_tax_guide",
        "plan_code": plan_code,
        "account_id": account_id,  # binds this payment to this user
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
            status_code=400,
            error="paystack_init_failed",
            stage="paystack_initialize",
            hint="Check PAYSTACK_SECRET_KEY and Paystack dashboard logs",
            root_cause=repr(e),
            extra={"reference": reference, "plan_code": plan_code},
            debug=debug,
        )

    data = (ps or {}).get("data") or {}
    auth_url = data.get("authorization_url")
    if not auth_url:
        return _fail(
            status_code=400,
            error="paystack_missing_authorization_url",
            stage="paystack_response",
            hint="Paystack init succeeded but did not return authorization_url",
            extra={"reference": reference, "paystack_data": data},
            debug=debug,
        )

    return jsonify(
        {
            "ok": True,
            "reference": reference,
            "authorization_url": auth_url,
            "access_code": data.get("access_code"),
            "plan": plan,
            "account_id": account_id,
            "email": email,  # safe to expose, helpful for UI/debug
            "db_warning": warn,
            "debug": debug,
        }
    ), 200


@bp.get("/billing/verify")
def billing_verify():
    """
    Verify a reference (after Paystack redirect).
    SECURE:
      - requires auth
      - ensures tx metadata account_id matches requester account_id
    GET /billing/verify?reference=...
    """
    requester_account_id, debug = get_account_id_from_request(request)
    if not requester_account_id:
        return _fail(
            status_code=401,
            error="unauthorized",
            stage="auth",
            hint="Missing or invalid bearer token",
            debug=debug,
        )

    reference = (request.args.get("reference") or "").strip()
    if not reference:
        return _fail(
            status_code=400,
            error="missing_reference",
            stage="validate_input",
            hint="Pass ?reference=... from Paystack redirect",
            debug=debug,
        )

    try:
        ps = verify_transaction(reference)
    except Exception as e:
        return _fail(
            status_code=400,
            error="paystack_verify_failed",
            stage="paystack_verify",
            hint="Check Paystack reference and PAYSTACK_SECRET_KEY",
            root_cause=repr(e),
            extra={"reference": reference},
            debug=debug,
        )

    tx = (ps or {}).get("data") or {}
    status = (tx.get("status") or "").strip().lower()
    tx_id = str(tx.get("id") or "") or None
    metadata = tx.get("metadata") or {}

    plan_code = (metadata.get("plan_code") or "").strip().lower()
    tx_account_id = (metadata.get("account_id") or "").strip()

    _store_paystack_event(event_id=tx_id, event_type="verify", reference=reference, payload=ps)

    if status != "success":
        return jsonify(
            {
                "ok": True,
                "paid": False,
                "status": status,
                "reference": reference,
                "account_id": requester_account_id,
                "data": tx,
                "debug": debug,
            }
        ), 200

    if not plan_code or not tx_account_id:
        return _fail(
            status_code=400,
            error="missing_metadata",
            stage="paystack_metadata",
            hint="Ensure /billing/checkout sets metadata.account_id and metadata.plan_code",
            extra={"reference": reference, "metadata": metadata},
            debug=debug,
        )

    if tx_account_id != requester_account_id:
        return _fail(
            status_code=403,
            error="forbidden_reference_owner_mismatch",
            stage="security_owner_check",
            hint="This reference belongs to a different account_id",
            extra={"reference": reference, "account_id": requester_account_id},
            debug=debug,
        )

    plan = get_plan(plan_code)
    if not plan:
        return _fail(
            status_code=404,
            error="plan_not_found",
            stage="validate_plan",
            hint="Plan code in metadata not found in plans_service",
            extra={"plan_code": plan_code, "reference": reference},
            debug=debug,
        )

    try:
        sub = _upsert_user_subscription(
            account_id=requester_account_id,
            plan_code=plan_code,
            duration_days=int(plan["duration_days"]),
            provider="paystack",
            provider_ref=reference,
        )
    except Exception as e:
        return _fail(
            status_code=500,
            error="subscription_upsert_failed",
            stage="db_user_subscriptions",
            hint="Check user_subscriptions table schema and RLS/service role access",
            root_cause=repr(e),
            extra={"account_id": requester_account_id, "plan_code": plan_code, "reference": reference},
            debug=debug,
        )

    return jsonify(
        {
            "ok": True,
            "paid": True,
            "reference": reference,
            "subscription": sub,
            "plan": plan,
            "account_id": requester_account_id,
            "debug": debug,
        }
    ), 200


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
        return _fail(
            status_code=401,
            error="invalid_signature",
            stage="webhook_signature",
            hint="Check PAYSTACK_WEBHOOK_SECRET and Paystack webhook config",
        )

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
                try:
                    _upsert_user_subscription(
                        account_id=account_id,
                        plan_code=plan_code,
                        duration_days=int(plan["duration_days"]),
                        provider="paystack",
                        provider_ref=reference,
                    )
                except Exception:
                    # webhook must not fail hard; we already stored event
                    pass

    return jsonify({"ok": True}), 200
