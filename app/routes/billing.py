from __future__ import annotations

from flask import Blueprint, jsonify, request

from ..services.plans_service import get_plan
from ..services.subscriptions_service import get_subscription_status
from ..services.web_sessions_service import validate_web_session, touch_session_best_effort

bp = Blueprint("billing", __name__)


def _get_bearer_token() -> str | None:
    auth = request.headers.get("Authorization", "") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return None


@bp.get("/billing/me")
def billing_me():
    token = _get_bearer_token()
    if not token:
        return jsonify({"ok": False, "error": "missing_token"}), 401

    sess = validate_web_session(token)
    if not sess:
        return jsonify({"ok": False, "error": "invalid_token"}), 401

    touch_session_best_effort(token)

    account_id = sess.get("account_id")
    provider = sess.get("provider") or "web"
    provider_user_id = sess.get("provider_user_id")

    sub = get_subscription_status(account_id, provider, provider_user_id)
    plan_code = sub.get("plan_code")
    plan = get_plan(plan_code) if plan_code else None

    reason_state = sub.get("reason") or sub.get("state")

    return jsonify(
        {
            "ok": True,
            "account_id": account_id,
            "provider": provider,
            "provider_user_id": provider_user_id,
            "reason": reason_state,
            "subscription": sub,
            "plan": plan,
            # ✅ Option A: do NOT expose credit/usage numbers to end-users.
            # The backend still enforces limits internally.
            "credits": {"hidden": True},
        }
    )
