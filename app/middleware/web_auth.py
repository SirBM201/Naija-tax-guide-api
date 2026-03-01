# app/middleware/web_auth.py
from __future__ import annotations

from functools import wraps
from typing import Any, Dict

from flask import request, jsonify, g

from app.core.supabase_client import supabase
from app.services.web_auth_service import get_account_id_from_request


def _load_subscription(account_id: str) -> Dict[str, Any]:
    """
    Pull subscription info for frontend billing panel.
    Table: user_subscriptions
    """
    try:
        res = (
            supabase.table("user_subscriptions")
            .select("account_id, plan_code, expires_at, grace_until, active, created_at, updated_at")
            .eq("account_id", account_id)
            .limit(1)
            .execute()
        )
        rows = (res.data or []) if hasattr(res, "data") else []
        if not rows:
            return {"active": False, "plan_code": None, "expires_at": None, "grace_until": None, "state": "none"}

        r = rows[0]
        active = bool(r.get("active"))
        state = "active" if active else "expired"

        return {
            "active": active,
            "plan_code": r.get("plan_code"),
            "expires_at": r.get("expires_at"),
            "grace_until": r.get("grace_until"),
            "state": state,
        }
    except Exception:
        return {"active": False, "plan_code": None, "expires_at": None, "grace_until": None, "state": "none"}


def _load_credits(account_id: str) -> Dict[str, Any]:
    """
    Table: ai_credit_balances(account_id, balance, updated_at)
    """
    try:
        res = (
            supabase.table("ai_credit_balances")
            .select("account_id, balance, updated_at")
            .eq("account_id", account_id)
            .limit(1)
            .execute()
        )
        rows = (res.data or []) if hasattr(res, "data") else []
        if not rows:
            return {"balance": 0}

        bal = rows[0].get("balance") or 0
        return {"balance": int(bal), "updated_at": rows[0].get("updated_at")}
    except Exception:
        return {"balance": 0}


def require_web_auth(fn):
    """
    Single Source of Truth web auth:
      - Uses app.services.web_auth_service.get_account_id_from_request()
        (supports Bearer OR Cookie)
      - Allows OPTIONS (CORS preflight)
      - Sets:
          g.account_id
          g.subscription
          g.credits
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        # ✅ Let CORS preflight pass
        if request.method == "OPTIONS":
            return ("", 204)

        account_id, debug = get_account_id_from_request(request)

        if not account_id:
            # Keep error stable for frontend:
            # - "unauthorized" + structured debug
            return jsonify({"ok": False, "error": "unauthorized", "debug": debug}), 401

        g.account_id = str(account_id).strip()

        # ✅ Enrich context for billing + ask routes
        g.subscription = _load_subscription(g.account_id)
        g.credits = _load_credits(g.account_id)

        return fn(*args, **kwargs)

    return wrapper
