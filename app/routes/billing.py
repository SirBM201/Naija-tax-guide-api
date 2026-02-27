# app/routes/billing.py
from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.services.plans_service import get_plan, list_plans
from app.services.web_auth_service import get_account_id_from_request

bp = Blueprint("billing", __name__)


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
    """
    Minimal auth probe endpoint for the frontend.
    Uses SAME cookie/bearer validation as /web/auth/me.
    """
    account_id, debug = get_account_id_from_request(request)
    if not account_id:
        return jsonify({"ok": False, "error": "unauthorized", "debug": debug}), 401

    # Keep it minimal. You can expand later to include subscription status.
    return jsonify({"ok": True, "account_id": account_id, "debug": debug}), 200
