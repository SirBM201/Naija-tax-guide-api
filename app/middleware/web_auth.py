# app/middleware/web_auth.py
from __future__ import annotations

from functools import wraps
from typing import Any, Dict, Tuple

from flask import request, jsonify, g

from ..core.supabase_client import supabase
from ..services.web_auth_service import get_account_id_from_request


def _load_subscription(accounts_id: str) -> Dict[str, Any]:
    """
    Pull subscription info for frontend billing panel.
    Table: user_subscriptions

    IMPORTANT:
    - accounts_id is the internal accounts.id (UUID) used as FK across tables.
    - We DO NOT use public account_id here.
    """
    try:
        res = (
            supabase.table("user_subscriptions")
            .select("account_id, plan_code, expires_at, grace_until, active, created_at, updated_at")
            .eq("account_id", accounts_id)
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


def _load_credits(accounts_id: str) -> Dict[str, Any]:
    """
    Table: ai_credit_balances(account_id, balance, updated_at)

    IMPORTANT:
    - accounts_id is the internal accounts.id (UUID) used as FK.
    """
    try:
        res = (
            supabase.table("ai_credit_balances")
            .select("account_id, balance, updated_at")
            .eq("account_id", accounts_id)
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


def _resolve_accounts_id(public_account_id: str) -> Tuple[str | None, Dict[str, Any]]:
    """
    get_account_id_from_request() returns a "public_account_id" that may be:
      - accounts.account_id (if present), OR
      - accounts.id (UUID), OR
      - web_tokens.account_id (legacy)

    We normalize to accounts.id for FK usage in subscription/credits tables.
    """
    v = (public_account_id or "").strip()
    if not v:
        return None, {"ok": False, "error": "invalid_account"}

    # If it looks like a UUID, assume it's accounts.id already.
    # (accounts.id is typically UUID in Supabase)
    if len(v) >= 32 and "-" in v:
        return v, {"ok": True, "source": "assumed_uuid"}

    # Otherwise resolve by accounts.account_id
    try:
        res = (
            supabase.table("accounts")
            .select("id, account_id")
            .eq("account_id", v)
            .limit(1)
            .execute()
        )
        rows = (res.data or []) if hasattr(res, "data") else []
        if not rows:
            return None, {"ok": False, "error": "account_not_found", "public_account_id": v}

        return str(rows[0].get("id") or ""), {"ok": True, "source": "accounts.account_id_lookup"}
    except Exception as e:
        return None, {"ok": False, "error": "account_lookup_failed", "root_cause": repr(e)}


def require_web_auth(fn):
    """
    Single source of truth auth middleware.

    ✅ Uses get_account_id_from_request(request) (cookie/bearer) for validation.
    ✅ Allows OPTIONS (CORS preflight) through without auth.
    ✅ Sets:
        g.account_id         -> public account id (string)
        g.accounts_id        -> internal accounts.id (UUID string)
        g.subscription       -> subscription snapshot (by accounts_id)
        g.credits            -> credits snapshot (by accounts_id)
        g.auth_debug         -> helpful debug bundle
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        # ✅ Let CORS preflight pass
        if request.method == "OPTIONS":
            return ("", 204)

        public_account_id, debug = get_account_id_from_request(request)
        if not public_account_id:
            # Keep response shape consistent with your other endpoints
            return jsonify({"ok": False, "error": "unauthorized", "debug": debug}), 401

        accounts_id, rid_dbg = _resolve_accounts_id(str(public_account_id))
        if not accounts_id:
            return jsonify({"ok": False, "error": "unauthorized", "debug": {"auth": debug, "resolve": rid_dbg}}), 401

        # ✅ Attach to Flask g
        g.account_id = str(public_account_id)   # public id
        g.accounts_id = str(accounts_id)        # FK-safe internal id

        # ✅ Enrich context for billing + ask routes
        g.subscription = _load_subscription(g.accounts_id)
        g.credits = _load_credits(g.accounts_id)

        g.auth_debug = {"auth": debug, "resolve": rid_dbg}

        return fn(*args, **kwargs)

    return wrapper
