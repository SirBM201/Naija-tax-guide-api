# app/core/auth.py
from __future__ import annotations

import hashlib
from functools import wraps
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Tuple

from flask import request, jsonify, g

from .supabase_client import supabase


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _sha256_hex(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _extract_bearer_token() -> str:
    """
    Accepts:
      Authorization: Bearer <token>
    """
    auth = (request.headers.get("Authorization") or "").strip()
    if not auth:
        return ""
    parts = auth.split(" ", 1)
    if len(parts) != 2:
        return ""
    scheme, token = parts[0].strip().lower(), parts[1].strip()
    if scheme != "bearer":
        return ""
    return token


def _validate_web_token(token: str) -> Tuple[bool, Optional[str], str]:
    """
    Validates token against public.web_sessions:
      - token_hash == sha256(token)
      - revoked_at IS NULL
      - expires_at > now() (if present)
    Returns: (ok, account_id, reason)
    """
    token = (token or "").strip()
    if not token:
        return False, None, "missing_token"

    token_hash = _sha256_hex(token)

    try:
        res = (
            supabase()
            .table("web_sessions")
            .select("account_id, expires_at, revoked_at")
            .eq("token_hash", token_hash)
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        if not rows:
            return False, None, "not_found"

        row = rows[0]

        if row.get("revoked_at"):
            return False, None, "revoked"

        expires_at = row.get("expires_at")
        if expires_at:
            try:
                exp = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
                if exp <= _now_utc():
                    return False, None, "expired"
            except Exception:
                return False, None, "bad_expires_at"

        account_id = row.get("account_id")
        if not account_id:
            return False, None, "missing_account_id"

        # touch last_seen_at (best-effort)
        try:
            supabase().table("web_sessions").update(
                {"last_seen_at": _now_utc().isoformat().replace("+00:00", "Z")}
            ).eq("token_hash", token_hash).execute()
        except Exception:
            pass

        return True, str(account_id), "ok"

    except Exception:
        return False, None, "server_error"


def require_web_auth(fn: Callable[..., Any]) -> Callable[..., Any]:
    """
    Flask decorator:
      - Validates Bearer token
      - Stores g.account_id
    """
    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any):
        token = _extract_bearer_token()
        ok, account_id, reason = _validate_web_token(token)

        if not ok or not account_id:
            return jsonify({"ok": False, "error": "invalid_token", "reason": reason}), 401

        g.account_id = account_id
        return fn(*args, **kwargs)

    return wrapper


def get_authed_account_id() -> Optional[str]:
    return getattr(g, "account_id", None)


# -------------------------------------------------------------------
# Backward compatible name used by existing routes (web_session.py etc.)
# -------------------------------------------------------------------
def require_auth_plus(fn: Callable[..., Any]) -> Callable[..., Any]:
    """
    Backward compatible alias used in older route modules.
    For now, it is identical to require_web_auth.
    """
    return require_web_auth(fn)
