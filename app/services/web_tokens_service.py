# app/services/web_tokens_service.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from flask import Request

from app.core.supabase_client import supabase
from app.core.auth import token_hash  # single source of truth for hashing


# Table name comes from env/config via core.auth (uses WEB_TOKEN_TABLE there),
# but we keep a default here too:
DEFAULT_TABLE = "web_sessions"


def _sb():
    return supabase() if callable(supabase) else supabase


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        v = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(v)
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _has_column(table: str, col: str) -> bool:
    try:
        _sb().table(table).select(col).limit(1).execute()
        return True
    except Exception:
        return False


# -----------------------------
# Token extraction
# -----------------------------
def extract_bearer_token(req: Request) -> Optional[str]:
    auth = (req.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        t = auth[7:].strip()
        return t or None

    t2 = (req.headers.get("X-Auth-Token") or "").strip()
    return t2 or None


# -----------------------------
# DB access
# -----------------------------
def _get_session_row_by_token(table: str, raw_token: str) -> Optional[Dict[str, Any]]:
    raw_token = (raw_token or "").strip()
    if not raw_token:
        return None

    th = token_hash(raw_token)
    try:
        res = (
            _sb()
            .table(table)
            .select("*")
            .eq("token_hash", th)
            .limit(1)
            .execute()
        )
        rows = (res.data or []) if hasattr(res, "data") else []
        return rows[0] if rows else None
    except Exception:
        return None


# -----------------------------
# Public API
# -----------------------------
def validate_token(
    raw_token: str,
    table: str = DEFAULT_TABLE,
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    """
    Returns:
      (ok, {"account_id": <uuid>, "token_row": <row>}, error)
    """
    raw_token = (raw_token or "").strip()
    table = (table or DEFAULT_TABLE).strip() or DEFAULT_TABLE

    if not raw_token:
        return False, {}, "missing_token"

    row = _get_session_row_by_token(table, raw_token)
    if not row:
        return False, {}, "invalid_token"

    # Support BOTH schemas:
    # - revoked (bool)
    # - revoked_at (timestamp)
    if row.get("revoked") is True:
        return False, {}, "token_revoked"
    if row.get("revoked_at"):
        return False, {}, "token_revoked"

    exp = _parse_iso(row.get("expires_at"))
    if not exp or exp <= _now_utc():
        return False, {}, "token_expired"

    account_id = (row.get("account_id") or "").strip()
    if not account_id:
        return False, {}, "invalid_token"

    return True, {"account_id": account_id, "token_row": row}, None


def revoke_token(
    raw_token: str,
    table: str = DEFAULT_TABLE,
) -> Tuple[bool, Optional[str]]:
    """
    Best-effort revoke. Idempotent.
    Supports:
      - revoked (bool)
      - revoked_at (timestamp)
    """
    raw_token = (raw_token or "").strip()
    table = (table or DEFAULT_TABLE).strip() or DEFAULT_TABLE

    if not raw_token:
        return False, "missing_token"

    # If token doesn't exist -> treat as already logged out
    row = _get_session_row_by_token(table, raw_token)
    if not row:
        return True, None

    th = token_hash(raw_token)

    updates: Dict[str, Any] = {}
    if _has_column(table, "revoked"):
        updates["revoked"] = True
    if _has_column(table, "revoked_at"):
        updates["revoked_at"] = _iso(_now_utc())

    # If neither column exists, we can't revoke reliably
    if not updates:
        return False, "revoke_not_supported"

    try:
        _sb().table(table).update(updates).eq("token_hash", th).execute()
        return True, None
    except Exception:
        return False, "logout_failed"
