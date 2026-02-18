# app/core/auth.py
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from functools import wraps
from typing import Optional

from flask import g, jsonify, request

from app.core.supabase_client import supabase


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _validate_web_token(token: str) -> Optional[str]:
    """
    Validates a web session token.

    IMPORTANT:
    - verify-otp creates sessions in `web_sessions`
    - token is stored hashed as token_hash (sha256 hex)
    """
    t = (token or "").strip()
    if not t:
        return None

    token_hash = _sha256_hex(t)

    # supabase() returns the client (singleton)
    sb = supabase()

    try:
        resp = (
            sb.table("web_sessions")
            .select("account_id, expires_at, revoked_at")
            .eq("token_hash", token_hash)
            .limit(1)
            .execute()
        )
        row = (resp.data or [None])[0]
    except Exception:
        # If anything goes wrong, treat as invalid token
        return None

    if not row:
        return None

    # revoked?
    if row.get("revoked_at"):
        return None

    # expired?
    exp = _parse_iso(row.get("expires_at"))
    if exp and exp <= _now_utc():
        return None

    account_id = row.get("account_id")
    if not account_id:
        return None

    # Best-effort "touch" last_seen_at (do not block request if it fails)
    try:
        sb.table("web_sessions").update(
            {"last_seen_at": _now_utc().isoformat().replace("+00:00", "Z")}
        ).eq("token_hash", token_hash).execute()
    except Exception:
        pass

    return account_id


def require_auth_plus(fn):
    """
    Checks Authorization: Bearer <token>
    Sets g.account_id if valid, otherwise returns 401.
    """

    @wraps(fn)
    def wrapper(*args, **kwargs):
        auth = request.headers.get("Authorization") or ""
        token = ""
        if auth.lower().startswith("bearer "):
            token = auth.split(" ", 1)[1].strip()

        account_id = _validate_web_token(token)
        if not account_id:
            return jsonify({"ok": False, "error": "invalid_token"}), 401

        g.account_id = account_id
        return fn(*args, **kwargs)

    return wrapper
