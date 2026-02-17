# app/middleware/web_auth.py
from __future__ import annotations

from functools import wraps
from typing import Any, Dict

from flask import request, jsonify, g

from ..services.web_auth_tokens import verify_access_token
from ..core.supabase_client import supabase


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
    Validates Bearer token created by your web auth flow.
    - Allows OPTIONS (CORS preflight) through without auth
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

        auth = (request.headers.get("Authorization") or "").strip()
        if not auth.lower().startswith("bearer "):
            return jsonify({"ok": False, "error": "missing_token"}), 401

        token = auth.split(" ", 1)[1].strip()
        payload = verify_access_token(token)

        if not payload or not payload.get("account_id"):
            return jsonify({"ok": False, "error": "invalid_token"}), 401

        account_id = str(payload["account_id"]).strip()
        if not account_id:
            return jsonify({"ok": False, "error": "invalid_account"}), 401

        g.account_id = account_id

        # ✅ Enrich context for billing + ask routes
        g.subscription = _load_subscription(account_id)
        g.credits = _load_credits(account_id)

        return fn(*args, **kwargs)

    return wrapper
