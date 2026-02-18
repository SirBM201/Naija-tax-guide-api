# app/core/auth.py
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Callable, Optional, Tuple

from flask import g, jsonify, request

from app.core.supabase_client import supabase


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        v = value.replace("Z", "+00:00")
        return datetime.fromisoformat(v)
    except Exception:
        return None


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _get_bearer_token() -> str:
    """
    Reads Authorization: Bearer <token>
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


def _validate_web_token(token: str) -> Optional[str]:
    """
    Validates a web session token by hashing and looking it up in `web_sessions`.
    Returns account_id if valid else None.
    """
    token = (token or "").strip()
    if not token:
        return None

    token_hash = _sha256_hex(token)
    sb = supabase()  # IMPORTANT: supabase is a callable in this codebase

    try:
        res = (
            sb.table("web_sessions")
            .select("account_id, expires_at, revoked_at")
            .eq("token_hash", token_hash)
            .limit(1)
            .execute()
        )
    except Exception:
        # Let caller treat as invalid (don’t leak internal errors)
        return None

    rows = getattr(res, "data", None) or []
    if not rows:
        return None

    row = rows[0]
    account_id = row.get("account_id")
    expires_at = _parse_iso(row.get("expires_at"))
    revoked_at = _parse_iso(row.get("revoked_at"))

    if not account_id:
        return None
    if revoked_at is not None:
        return None
    if expires_at is not None and expires_at <= _now_utc():
        return None

    # Optional: touch last_seen_at if the column exists (best-effort)
    try:
        sb.table("web_sessions").update({"last_seen_at": _now_utc().isoformat().replace("+00:00", "Z")}).eq(
            "token_hash", token_hash
        ).execute()
    except Exception:
        pass

    return str(account_id)


def require_auth_plus(fn: Callable[..., Any]) -> Callable[..., Any]:
    """
    Protects endpoints using web bearer token.
    Sets:
      g.account_id
    """

    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any):
        token = _get_bearer_token()
        account_id = _validate_web_token(token)

        if not account_id:
            return jsonify({"ok": False, "error": "invalid_token"}), 401

        g.account_id = account_id
        return fn(*args, **kwargs)

    return wrapper
