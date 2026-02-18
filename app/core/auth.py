# app/core/auth.py

from __future__ import annotations

import hashlib
from functools import wraps
from datetime import datetime, timezone
from typing import Callable, Any, Dict

from flask import request, jsonify, g

from .supabase_client import supabase


# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _extract_bearer_token() -> str | None:
    """
    Canonical token extractor.

    Handles:
    - Bearer <token>
    - whitespace
    - casing
    - accidental newlines
    """

    auth_header = request.headers.get("Authorization", "")

    if not auth_header:
        return None

    parts = auth_header.strip().split()

    if len(parts) != 2:
        return None

    scheme, token = parts

    if scheme.lower() != "bearer":
        return None

    return token.strip()


def _validate_session(token: str) -> Dict[str, Any] | None:
    """
    Validate token against web_sessions table.
    """

    token_hash = _hash_token(token)

    res = (
        supabase()
        .table("web_sessions")
        .select("account_id, expires_at, revoked_at, token_hash")
        .eq("token_hash", token_hash)
        .limit(1)
        .execute()
    )

    if not res.data:
        return None

    row = res.data[0]

    if row.get("revoked_at"):
        return None

    expires_at = row.get("expires_at")
    if expires_at:
        exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        if exp < _now_utc():
            return None

    return row


# ---------------------------------------------------------
# Decorator
# ---------------------------------------------------------

def require_web_auth(fn: Callable) -> Callable:
    """
    Primary auth decorator.
    """

    @wraps(fn)
    def wrapper(*args, **kwargs):

        token = _extract_bearer_token()

        if not token:
            return jsonify({
                "ok": False,
                "error": "invalid_token",
                "reason": "missing_bearer"
            }), 401

        session_row = _validate_session(token)

        if not session_row:
            return jsonify({
                "ok": False,
                "error": "invalid_token",
                "reason": "not_found"
            }), 401

        # Attach to Flask context
        g.account_id = session_row["account_id"]
        g.auth_token = token
        g.token_row = session_row

        return fn(*args, **kwargs)

    return wrapper


# ---------------------------------------------------------
# Backward compatibility alias
# ---------------------------------------------------------

def require_auth_plus(fn: Callable) -> Callable:
    """
    Legacy alias used across routes.
    """
    return require_web_auth(fn)
