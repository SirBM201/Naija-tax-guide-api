# app/core/auth.py
from __future__ import annotations

import functools
import hashlib
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Tuple

from flask import g, jsonify, request

from app.core.config import WEB_TOKEN_PEPPER, WEB_TOKEN_TABLE
from app.core.supabase_client import supabase


def _sb():
    return supabase() if callable(supabase) else supabase


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


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _token_hash(raw_token: str) -> str:
    return _sha256_hex(f"{WEB_TOKEN_PEPPER}:{raw_token}")


def _extract_bearer_token() -> Optional[str]:
    auth = (request.headers.get("Authorization") or "").strip()
    if not auth:
        return None
    if auth.lower().startswith("bearer "):
        tok = auth[7:].strip()
        return tok or None
    return None


def _validate_web_token(raw_token: str) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
    """
    Validate a web session token from WEB_TOKEN_TABLE.

    Expects WEB_TOKEN_TABLE rows like:
      - token_hash (text)
      - account_id (uuid)   (this can be your accounts.id)
      - expires_at (timestamptz/iso string)
      - revoked (bool)
    """
    try:
        th = _token_hash(raw_token)

        res = (
            _sb()
            .table(WEB_TOKEN_TABLE)
            .select("*")
            .eq("token_hash", th)
            .eq("revoked", False)
            .limit(1)
            .execute()
        )

        rows = (res.data or []) if hasattr(res, "data") else []
        if not rows:
            return False, None, None

        row = rows[0]
        exp = _parse_iso(row.get("expires_at"))
        if exp and _now_utc() > exp:
            return False, None, row

        account_id = row.get("account_id")
        if not account_id:
            return False, None, row

        return True, str(account_id), row
    except Exception:
        return False, None, None


def require_auth_plus(fn: Callable[..., Any]) -> Callable[..., Any]:
    """
    Web auth decorator:
    - Reads Authorization: Bearer <token>
    - Validates against WEB_TOKEN_TABLE (hashed token)
    - Sets:
        g.account_id = <uuid>
        g.web_token_row = <row>
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        raw_token = _extract_bearer_token()
        if not raw_token:
            return jsonify({"ok": False, "error": "missing_token"}), 401

        ok, account_id, row = _validate_web_token(raw_token)
        if not ok or not account_id:
            return jsonify({"ok": False, "error": "invalid_token"}), 401

        g.account_id = account_id
        g.web_token_row = row
        return fn(*args, **kwargs)

    return wrapper
