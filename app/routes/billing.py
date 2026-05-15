from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional, Tuple
import json
import logging

from flask import Blueprint, jsonify, request, session

from app.core.supabase_client import supabase_client as supabase
from app.services.plans_service import get_plan, list_plans
from app.services.web_auth_service import get_account_id_from_request
from app.services.auth_service import get_current_user
from app.services.paystack_service import (
    create_reference,
    initialize_transaction,
    verify_transaction,
    verify_webhook_signature,
)
from app.services.credits_service import (
    init_credits_for_plan,
    get_credit_balance_details,
    get_daily_usage,
)
from app.services.subscription_guard import get_subscription_snapshot
from app.services.referral_service import ensure_referral_profile, qualify_referral_after_successful_payment
from app.services.channel_post_payment_service import notify_channel_payment_success

bp = Blueprint("billing", __name__)
logger = logging.getLogger(__name__)


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


def _get_account_id_from_session() -> Optional[str]:
    """Get account ID from Flask session first."""
    user_id = session.get("user_id")
    if user_id:
        logger.info(f"Account ID from session: {user_id}")
        return user_id
    return None


# -------------------- ROUTES --------------------


@bp.get("/plans")
def billing_plans():
    active_only = (request.args.get("active_only") or "1").strip() != "0"
    plans = list_plans(active_only=active_only)
    return jsonify({"ok": True, "plans": plans}), 200


@bp.get("/plans/<plan_code>")
def billing_plan(plan_code: str):
    p = get_plan(plan_code)
    if not p:
        return jsonify({"ok": False, "error": "plan_not_found"}), 404
    return jsonify({"ok": True, "plan": p}), 200


@bp.get("/me")
@bp.get("/subscription")
def billing_me():
    account_id = _get_account_id_from_session()
    
    if not account_id:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    
    return jsonify({
        "ok": True,
        "account_id": account_id,
        "plan_code": "free",
        "plan_name": "Free Plan",
        "status": "active",
        "active": True,
        "expires_at": None,
        "credit_balance": 0,
        "daily_usage_count": 0,
        "daily_answers_limit": 0,
        "ai_used_month": 0,
        "included_credits": 0
    }), 200


@bp.get("/debug-state")
def billing_debug_state():
    account_id = _get_account_id_from_session()
    
    if not account_id:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    
    return jsonify({
        "ok": True,
        "account_id": account_id,
        "subscription_guard_snapshot": {
            "access": {"allowed": True},
            "plan_code": "free",
            "daily_answers_limit": 0
        },
        "credit_balance": {"balance": 0, "used": 0},
        "daily_usage_today": {"count": 0},
        "whatsapp_linked": False,
        "telegram_linked": False,
        "whatsapp_verified": False,
        "telegram_verified": False
    }), 200


@bp.post("/checkout")
def billing_checkout():
    return jsonify({"ok": False, "error": "Not implemented yet"}), 501


@bp.post("/webhook")
def billing_webhook():
    return jsonify({"ok": True, "message": "Webhook received"}), 200
