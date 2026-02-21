# app/services/web_tokens_service.py
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from flask import Request
from app.core.supabase_client import supabase
from app.core.auth import token_hash  # IMPORTANT: single source of truth


# Your real table/columns (confirmed)
DEFAULT_TABLE = "web_sessions"
COL_ACCOUNT_ID = "account_id"
COL_EXPIRES_AT = "expires_at"
COL_TOKEN_HASH = "token_hash"

# In your DB, "revoked" is boolean. "revoked_at" may or may not exist.
COL_REVOKED_BOOL = "revoked"
COL_REVOKED_AT = "revoked_at"
COL_LAST_SEEN_AT = "last_seen_at"


def _sb():
    return supabase() if callable(supabase) else supabase


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
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


def _truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _cookie_name() -> str:
    return (os.getenv("WEB_COOKIE_NAME", "ntg_session") or "ntg_session").strip()


def _table_name() -> str:
    return (os.getenv("WEB_TOKEN_TABLE", DEFAULT_TABLE) or DEFAULT_TABLE).strip()


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


def extract_cookie_token(req: Request) -> Optional[str]:
    return req.cookies.get(_cookie_name())


def extract_any_token(req: Request) -> Tuple[Optional[str], Optional[str]]:
    """
    Returns (token, source) where source is 'bearer' or 'cookie'
    """
    t = extract_bearer_token(req)
    if t:
        return t, "bearer"
    c = extract_cookie_token(req)
    if c:
        return c, "cookie"
    return None, None


# -----------------------------
# Column detection (best-effort)
# -----------------------------
def _has_column(table: str, col: str) -> bool:
    try:
        _sb().table(table).select(col).limit(1).execute()
        return True
    except Exception:
        return False


# -----------------------------
# DB access
# -----------------------------
def _get_session_row_by_token(table: str, raw_token: str) -> Optional[Dict[str, Any]]:
    th = token_hash(raw_token)
    if not th:
        return None

    try:
        res = (
            _sb()
            .table(table)
            .select("*")
            .eq(COL_TOKEN_HASH, th)
            .limit(1)
            .execute()
        )
        rows = (res.data or []) if hasattr(res, "data") else []
        return rows[0] if rows else None
    except Exception:
        return None


def _touch_last_seen_best_effort(table: str, th: str) -> None:
    if not _has_column(table, COL_LAST_SEEN_AT):
        return
    try:
        _sb().table(table).update({COL_LAST_SEEN_AT: _iso(_now_utc())}).eq(COL_TOKEN_HASH, th).execute()
    except Exception:
        return


# -----------------------------
# Public API
# -----------------------------
def validate_token(raw_token: str) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    raw_token = (raw_token or "").strip()
    if not raw_token:
        return False, {}, "Unauthorized"

    table = _table_name()
    row = _get_session_row_by_token(table, raw_token)
    if not row:
        return False, {}, "Unauthorized"

    # revoked?
    if row.get(COL_REVOKED_BOOL) is True:
        return False, {}, "Session expired"
    if row.get(COL_REVOKED_AT):
        # in case your schema uses revoked_at instead of revoked bool
        return False, {}, "Session expired"

    exp = _parse_iso(row.get(COL_EXPIRES_AT))
    if not exp or exp <= _now_utc():
        return False, {}, "Session expired"

    account_id = (row.get(COL_ACCOUNT_ID) or "").strip()
    if not account_id:
        return False, {}, "Unauthorized"

    # touch last_seen_at best-effort
    th = token_hash(raw_token)
    _touch_last_seen_best_effort(table, th)

    return True, {"account_id": account_id, "token_row": row}, None


def revoke_token(raw_token: str) -> Tuple[bool, Optional[str]]:
    raw_token = (raw_token or "").strip()
    if not raw_token:
        return False, "Unauthorized"

    table = _table_name()
    row = _get_session_row_by_token(table, raw_token)
    if not row:
        return True, None  # idempotent

    th = token_hash(raw_token)
    updates: Dict[str, Any] = {}

    # your schema uses revoked bool
    if _has_column(table, COL_REVOKED_BOOL):
        updates[COL_REVOKED_BOOL] = True
    # optional revoked_at
    if _has_column(table, COL_REVOKED_AT):
        updates[COL_REVOKED_AT] = _iso(_now_utc())

    if not updates:
        # nothing to update, but treat as ok
        return True, None

    try:
        _sb().table(table).update(updates).eq(COL_TOKEN_HASH, th).execute()
        return True, None
    except Exception:
        return False, "Failed to logout"
