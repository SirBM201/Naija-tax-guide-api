# app/core/auth.py
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from functools import wraps
from typing import Optional

from flask import jsonify, request, g

# IMPORTANT:
# Your supabase_client module must export a *client object* named `supabase`.
# (not a function). This matches how the rest of your app uses it.
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


def _token_hash(token: str) -> str:
    # Must match how /web/auth/me stores/queries token_hash (sha256 hex)
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _validate_web_token(token: str) -> Optional[str]:
    """
    Validates the Bearer token using web_sessions (same approach as /web/auth/me).
    Returns account_id if valid, else None.
    """
    token = (token or "").strip()
    if not token:
        return None

    th = _token_hash(token)

    # Query: web_sessions?token_hash=eq.<hash>&limit=1
    res = (
        supabase.table("web_sessions")
        .select("account_id, expires_at, revoked_at")
        .eq("token_hash", th)
        .limit(1)
        .execute()
    )

    rows = getattr(res, "data", None) or []
    if not rows:
        return None

    row = rows[0]
    if row.get("revoked_at"):
        return None

    exp = _parse_iso(row.get("expires_at"))
    if exp and exp <= _now_utc():
        return None

    # Optional: update last_seen_at (best-effort)
    try:
        supabase.table("web_sessions").update(
            {"last_seen_at": _now_utc().isoformat().replace("+00:00", "Z")}
        ).eq("token_hash", th).execute()
    except Exception:
        pass

    return row.get("account_id")


def require_auth_plus(fn):
    """
    Reads Authorization: Bearer <token>
    Validates token via web_sessions
    Sets g.account_id
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        auth = (request.headers.get("Authorization") or "").strip()
        token = ""

        if auth.lower().startswith("bearer "):
            token = auth.split(" ", 1)[1].strip()

        account_id = _validate_web_token(token)
        if not account_id:
            return jsonify({"ok": False, "error": "invalid_token"}), 401

        g.account_id = account_id
        return fn(*args, **kwargs)

    return wrapper
