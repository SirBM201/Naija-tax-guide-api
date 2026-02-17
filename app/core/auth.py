# app/core/auth.py
from __future__ import annotations

import os
import hashlib
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Callable, Dict, Optional, List

from flask import g, request, jsonify

from app.core.supabase_client import supabase


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


# -----------------------------
# ENV (your real schema)
# -----------------------------
WEB_TOKEN_TABLE = (os.getenv("WEB_TOKEN_TABLE", "web_sessions") or "web_sessions").strip()

# Column that stores the token value (often token_hash)
WEB_TOKEN_COL_TOKEN = (os.getenv("WEB_TOKEN_COL_TOKEN", "token_hash") or "token_hash").strip()

WEB_TOKEN_COL_ACCOUNT_ID = (os.getenv("WEB_TOKEN_COL_ACCOUNT_ID", "account_id") or "account_id").strip()
WEB_TOKEN_COL_EXPIRES_AT = (os.getenv("WEB_TOKEN_COL_EXPIRES_AT", "expires_at") or "expires_at").strip()
WEB_TOKEN_COL_REVOKED_AT = (os.getenv("WEB_TOKEN_COL_REVOKED_AT", "revoked_at") or "revoked_at").strip()

# If set, we will prefer peppered hashing: sha256(f"{pepper}:{token}")
WEB_TOKEN_PEPPER = (os.getenv("WEB_TOKEN_PEPPER", "") or "").strip()


# -----------------------------
# Token helpers
# -----------------------------
def _bearer_token() -> str:
    auth = (request.headers.get("Authorization") or "").strip()
    if not auth:
        return ""
    parts = auth.split(" ", 1)
    if len(parts) != 2:
        return ""
    if parts[0].lower() != "bearer":
        return ""
    return parts[1].strip()


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _candidate_token_values(token: str) -> List[str]:
    """
    We try multiple formats because different code versions may store tokens differently:
      1) sha256(f"{pepper}:{token}")   (preferred if pepper is set)
      2) sha256(token)                (common older/simple approach)
      3) raw token                    (if DB stores the plain token)
    """
    if not token:
        return []

    out: List[str] = []
    if WEB_TOKEN_PEPPER:
        out.append(_sha256_hex(f"{WEB_TOKEN_PEPPER}:{token}"))
    out.append(_sha256_hex(token))
    out.append(token)

    # de-dup while preserving order
    seen = set()
    uniq: List[str] = []
    for v in out:
        if v and v not in seen:
            uniq.append(v)
            seen.add(v)
    return uniq


def _get_token_row(token: str) -> Optional[Dict[str, Any]]:
    """
    Finds token row in WEB_TOKEN_TABLE by matching WEB_TOKEN_COL_TOKEN
    against possible stored formats.
    Expected schema shape: web_sessions(token_hash(or token), account_id, expires_at, revoked_at)
    """
    if not token:
        return None

    candidates = _candidate_token_values(token)
    if not candidates:
        return None

    try:
        q = (
            supabase.table(WEB_TOKEN_TABLE)
            .select(
                f"{WEB_TOKEN_COL_ACCOUNT_ID}, {WEB_TOKEN_COL_EXPIRES_AT}, "
                f"{WEB_TOKEN_COL_REVOKED_AT}, {WEB_TOKEN_COL_TOKEN}"
            )
        )

        # Supabase python supports .in_ for WHERE IN
        q = q.in_(WEB_TOKEN_COL_TOKEN, candidates).limit(1)

        res = q.execute()
        rows = (res.data or []) if hasattr(res, "data") else []
        return rows[0] if rows else None
    except Exception:
        return None


def _parse_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        # ensure tz-aware
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        s = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _is_token_valid(row: Dict[str, Any]) -> bool:
    if not row:
        return False

    if row.get(WEB_TOKEN_COL_REVOKED_AT):
        return False

    exp_dt = _parse_dt(row.get(WEB_TOKEN_COL_EXPIRES_AT))
    if not exp_dt:
        return False

    return exp_dt > _now_utc()


# -----------------------------
# Subscription + Credits loaders
# -----------------------------
def _load_subscription(account_id: str) -> Dict[str, Any]:
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


# -----------------------------
# Decorator (works with or without parentheses)
# -----------------------------
def require_auth_plus(fn: Optional[Callable] = None) -> Callable:
    """
    Use:
      @require_auth_plus
    or:
      @require_auth_plus()

    Allows:
      - OPTIONS requests (CORS preflight)
    Sets:
      g.account_id
      g.auth_token
      g.token_row
      g.subscription
      g.credits
    """

    def decorator(view_func: Callable) -> Callable:
        @wraps(view_func)
        def wrapper(*args, **kwargs):
            # ✅ allow CORS preflight
            if request.method == "OPTIONS":
                return ("", 204)

            token = _bearer_token()
            if not token:
                return jsonify({"ok": False, "error": "missing_token"}), 401

            row = _get_token_row(token)
            if not row:
                return jsonify({"ok": False, "error": "invalid_token"}), 401

            if not _is_token_valid(row):
                return jsonify({"ok": False, "error": "expired_or_revoked"}), 401

            account_id = (row.get(WEB_TOKEN_COL_ACCOUNT_ID) or "").strip()
            if not account_id:
                return jsonify({"ok": False, "error": "invalid_account"}), 401

            g.account_id = account_id
            g.auth_token = token
            g.token_row = {
                "expires_at": row.get(WEB_TOKEN_COL_EXPIRES_AT),
                "revoked_at": row.get(WEB_TOKEN_COL_REVOKED_AT),
            }

            g.subscription = _load_subscription(account_id)
            g.credits = _load_credits(account_id)

            return view_func(*args, **kwargs)

        return wrapper

    if callable(fn):
        return decorator(fn)
    return decorator


# Backward-compat alias (some routes still reference it)
require_web_auth = require_auth_plus
