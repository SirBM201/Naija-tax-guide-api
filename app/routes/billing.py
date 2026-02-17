"""Billing snapshot for the web UI.

Frontend calls:
  GET /api/billing/me

Source of truth:
  - Subscription state: public.user_subscriptions (via services/subscriptions_service.py)
  - Plan metadata: public.plans
  - Credits: public.ai_credit_ledger

IMPORTANT:
  Ignore any legacy table named public.subscriptions. Your app logic uses
  public.user_subscriptions.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from ..core.supabase_client import supabase
from ..services.credit_ledger_service import get_latest_credit_row
from ..services.subscriptions_service import get_subscription_status
from ..services.web_sessions_service import validate_web_session, touch_session_best_effort

bp = Blueprint("billing", __name__)


def _bearer_token() -> str | None:
    auth = (request.headers.get("Authorization") or "").strip()
    if not auth:
        return None
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip() or None
    return None


@bp.get("/billing/me")
def billing_me():
    """Return the logged-in user's billing snapshot."""
    token = _bearer_token()
    if not token:
        return jsonify({"ok": False, "error": "missing_token"}), 401

    ok, account_id, reason = validate_web_session(token)
    if not ok or not account_id:
        return jsonify({"ok": False, "error": reason}), 401

    touch_session_best_effort(token)

    # -------------------------
    # Subscription (SOURCE OF TRUTH = user_subscriptions)
    # -------------------------
    sub = get_subscription_status(account_id=account_id)
    active = bool(sub.get("active"))
    state = sub.get("state") or "none"
    plan_code = sub.get("plan_code")
    expires_at = sub.get("expires_at")
    grace_until = sub.get("grace_until")
    reason_state = sub.get("reason") or "unknown"

    # Optional plan metadata for the UI
    plan = None
    if plan_code:
        try:
            sb = supabase()
            resp = (
                sb.table("plans")
                .select("plan_code,name,duration_days,active,ai_credits_total,daily_answers_limit,created_at")
                .eq("plan_code", plan_code)
                .limit(1)
                .execute()
            )
            rows = getattr(resp, "data", None) or []
            plan = rows[0] if rows else None
        except Exception:
            plan = None

    # -------------------------
    # Credits (ai_credit_ledger)
    # -------------------------
    credit_row = get_latest_credit_row(account_id)
    credits = None
    if credit_row:
        credits = {
            "credits_total": credit_row.get("credits_total"),
            "credits_remaining": credit_row.get("credits_remaining"),
            "daily_answers_limit": credit_row.get("daily_answers_limit"),
            "daily_answers_used": credit_row.get("daily_answers_used"),
            "daily_day": credit_row.get("daily_day"),
            "updated_at": credit_row.get("updated_at"),
        }
    else:
        credits = {
            "credits_total": 0,
            "credits_remaining": 0,
            "daily_answers_limit": None,
            "daily_answers_used": 0,
            "daily_day": None,
            "updated_at": None,
        }

    return jsonify(
        {
            "ok": True,
            "account_id": account_id,
            "active": active,
            "state": state,  # none | active | grace | expired
            "plan_code": plan_code,
            "expires_at": expires_at,
            "grace_until": grace_until,
            "reason": reason_state,
            "subscription": sub,
            "plan": plan,
            "credits": credits,
        }
    )
