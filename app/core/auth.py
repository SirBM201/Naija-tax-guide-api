# app/core/auth.py
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Callable, Dict, Optional, Tuple

from flask import g, request

from app.core.config import WEB_AUTH_ENABLED, WEB_TOKEN_PEPPER, WEB_TOKEN_TABLE
from app.core.supabase_client import supabase


# -----------------------------
# Token helpers
# -----------------------------

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: str) -> Optional[datetime]:
    try:
        v = (value or "").replace("Z", "+00:00")
        return datetime.fromisoformat(v)
    except Exception:
        return None


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _normalize_bearer(auth_header: str) -> str:
    """Extract raw token from Authorization: Bearer <token>."""
    if not auth_header:
        return ""
    v = auth_header.strip()
    if v.lower().startswith("bearer "):
        return v[7:].strip()
    return ""


def _token_hash(raw_token: str) -> str:
    """
    MUST match the hashing scheme used when creating sessions.

    Sessions are stored as:
      token_hash = sha256(f"{WEB_TOKEN_PEPPER}:{raw_token}")

    If any route hashes differently, you'll get 'invalid_token'.
    """
    return _sha256_hex(f"{WEB_TOKEN_PEPPER}:{raw_token}")


# -----------------------------
# Session validation
# -----------------------------

def validate_web_session(raw_token: str) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    if not WEB_AUTH_ENABLED:
        return False, None, "web_auth_disabled"

    if not raw_token:
        return False, None, "missing_token"

    token_hash = _token_hash(raw_token)

    # supabase can be either a client instance OR a callable factory, depending on your setup
    sb = supabase() if callable(supabase) else supabase

    q = (
        sb.table(WEB_TOKEN_TABLE)
        .select("id, account_id, expires_at, revoked")
        .eq("token_hash", token_hash)
        .eq("revoked", False)
        .limit(1)
        .execute()
    )

    if not getattr(q, "data", None):
        return False, None, "invalid_token"

    row = q.data[0]

    exp = _parse_iso(row.get("expires_at") or "")
    if not exp or _now_utc() > exp:
        sb.table(WEB_TOKEN_TABLE).update({"revoked": True}).eq("id", row["id"]).execute()
        return False, None, "token_expired"

    return True, row, "ok"


def touch_session(raw_token: str) -> None:
    """Best-effort: update last_seen_at without breaking requests."""
    if not raw_token:
        return

    try:
        sb = supabase() if callable(supabase) else supabase
        token_hash = _token_hash(raw_token)
        sb.table(WEB_TOKEN_TABLE).update({"last_seen_at": _now_utc().isoformat()}).eq(
            "token_hash", token_hash
        ).execute()
    except Exception:
        return


# -----------------------------
# Decorator
# -----------------------------

def require_auth_plus(fn: Callable) -> Callable:
    """Flask decorator: validates bearer token and sets g.account_id."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        raw_token = _normalize_bearer(auth)

        ok, row, reason = validate_web_session(raw_token)
        if not ok:
            return {"ok": False, "error": reason}, 401

        g.web_token = raw_token
        g.account_id = row.get("account_id")

        touch_session(raw_token)

        return fn(*args, **kwargs)

    return wrapper
