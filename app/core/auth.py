# app/core/auth.py
from __future__ import annotations

import hashlib
import os
import traceback
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Callable, Optional, Dict

from flask import g, jsonify, request

from app.core.supabase_client import supabase
from app.core.config import WEB_TOKEN_TABLE, WEB_TOKEN_PEPPER


def _sb():
    return supabase() if callable(supabase) else supabase


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def get_web_token_pepper() -> str:
    """
    Single source of truth for the pepper used in BOTH:
    - issuing tokens (web_auth.py)
    - validating tokens (require_auth_plus)

    Priority:
      1) env WEB_TOKEN_PEPPER
      2) config WEB_TOKEN_PEPPER
    """
    return (os.getenv("WEB_TOKEN_PEPPER", WEB_TOKEN_PEPPER) or WEB_TOKEN_PEPPER).strip()


def token_hash(raw_token: str) -> str:
    """
    Single source of truth for token hashing.
    """
    pepper = get_web_token_pepper()
    return _sha256_hex(f"{pepper}:{raw_token}")


def _get_bearer_token() -> Optional[str]:
    auth = (request.headers.get("Authorization") or "").strip()
    if not auth:
        return None
    parts = auth.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def _truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _auth_debug_enabled() -> bool:
    # Set AUTH_DEBUG=1 on Koyeb to enable debug prints (safe, no raw tokens)
    return _truthy(os.getenv("AUTH_DEBUG"))


def _dbg(msg: str) -> None:
    if _auth_debug_enabled():
        print(msg, flush=True)


def auth_debug_snapshot() -> Dict[str, Any]:
    """
    Safe debug snapshot: NO secrets.
    """
    pepper = get_web_token_pepper()
    return {
        "web_token_table": (os.getenv("WEB_TOKEN_TABLE", WEB_TOKEN_TABLE) or WEB_TOKEN_TABLE),
        "pepper_len": len(pepper),
        "pepper_prefix_sha256": _sha256_hex(pepper)[:12],
        "auth_debug": _auth_debug_enabled(),
    }


def require_auth_plus(fn: Callable[..., Any]) -> Callable[..., Any]:
    """
    Validates web session tokens stored in WEB_TOKEN_TABLE.
    Sets:
      g.account_id = <uuid string from web_tokens.account_id>
      g.web_token_hash = <hashed token>
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        raw = _get_bearer_token()
        if not raw:
            _dbg("[auth] missing_token: no Authorization: Bearer <token> header")
            return jsonify({"ok": False, "error": "missing_token"}), 401

        th = token_hash(raw)
        th_prefix = th[:12]  # safe prefix for correlation
        table = (os.getenv("WEB_TOKEN_TABLE", WEB_TOKEN_TABLE) or WEB_TOKEN_TABLE).strip()

        try:
            _dbg(f"[auth] start token_hash_prefix={th_prefix} path={request.path} method={request.method}")

            res = (
                _sb()
                .table(table)
                .select("account_id, expires_at, revoked")
                .eq("token_hash", th)
                .limit(1)
                .execute()
            )
            rows = (res.data or []) if hasattr(res, "data") else []
            if not rows:
                _dbg(f"[auth] invalid_token: token_hash_prefix={th_prefix} not found in {table}")
                return jsonify({"ok": False, "error": "invalid_token"}), 401

            row = rows[0]
            if row.get("revoked") is True:
                _dbg(f"[auth] token_revoked: token_hash_prefix={th_prefix}")
                return jsonify({"ok": False, "error": "token_revoked"}), 401

            expires_at = row.get("expires_at")
            if expires_at:
                v = str(expires_at).replace("Z", "+00:00")
                exp_dt = datetime.fromisoformat(v).astimezone(timezone.utc)
                if _now_utc() > exp_dt:
                    _dbg(f"[auth] token_expired: token_hash_prefix={th_prefix} exp={exp_dt.isoformat()}")
                    return jsonify({"ok": False, "error": "token_expired"}), 401

            # touch last_seen_at best-effort
            try:
                _sb().table(table).update(
                    {"last_seen_at": _now_utc().isoformat()}
                ).eq("token_hash", th).execute()
            except Exception as e:
                _dbg(f"[auth] last_seen_at update skipped: {type(e).__name__}: {str(e)[:160]}")

            g.account_id = row.get("account_id")
            g.web_token_hash = th

            _dbg(f"[auth] ok account_id={g.account_id} token_hash_prefix={th_prefix}")
            return fn(*args, **kwargs)

        except Exception as e:
            _dbg(f"[auth] auth_failed: {type(e).__name__}: {str(e)[:220]}")
            _dbg("[auth] traceback:\n" + traceback.format_exc())
            return jsonify({"ok": False, "error": "auth_failed"}), 401

    return wrapper
