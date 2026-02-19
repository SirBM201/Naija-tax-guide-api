# app/services/web_sessions_service.py
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from ..core.supabase_client import supabase


# ------------------------------------------------------------
# Config
# ------------------------------------------------------------

WEB_SESSION_TTL_DAYS = int((os.getenv("WEB_SESSION_TTL_DAYS", "30") or "30").strip())
WEB_SESSION_TOUCH_ENABLED = (os.getenv("WEB_SESSION_TOUCH_ENABLED", "1").strip() == "1")


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)

def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        v = value.replace("Z", "+00:00")
        return datetime.fromisoformat(v)
    except Exception:
        return None

def _sb():
    try:
        return supabase()
    except TypeError:
        return supabase

def _table(name: str):
    return _sb().table(name)

def _clean(s: Any) -> str:
    return (s or "").strip()


# ------------------------------------------------------------
# Public API (MUST match app/routes/web_auth.py imports)
# ------------------------------------------------------------

def validate_web_session(token: str) -> Tuple[bool, Optional[str], str]:
    """
    Validates web session token.

    Returns: (ok, account_id, reason)

    Table recommended: web_sessions
      - token (text pk)
      - contact (text)
      - account_id (text/uuid nullable)
      - expires_at (timestamptz)
      - created_at (timestamptz)
      - last_seen_at (timestamptz)
      - revoked_at (timestamptz nullable)
    """
    token = _clean(token)
    if not token:
        return False, None, "missing_token"

    # Best effort: if table not present, fail closed (safer)
    try:
        res = (
            _table("web_sessions")
            .select("token, account_id, expires_at, revoked_at")
            .eq("token", token)
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        if not rows:
            return False, None, "session_not_found"

        row = rows[0]
        revoked_at = _parse_iso(row.get("revoked_at"))
        if revoked_at:
            return False, None, "session_revoked"

        expires_at = _parse_iso(row.get("expires_at"))
        if not expires_at:
            return False, None, "session_missing_expiry"

        if _now_utc() > expires_at:
            return False, None, "session_expired"

        account_id = _clean(row.get("account_id")) or None
        # NOTE: if account_id is not yet linked, you can still treat it as logged-in user
        # by linking contact->account later. For now, your /me expects account_id.
        if not account_id:
            return False, None, "session_not_linked"

        return True, account_id, "ok"
    except Exception:
        return False, None, "session_store_unavailable"


def touch_session_best_effort(token: str) -> None:
    """
    Updates last_seen_at for a session. Never throws.
    """
    if not WEB_SESSION_TOUCH_ENABLED:
        return

    token = _clean(token)
    if not token:
        return

    try:
        _table("web_sessions").update({"last_seen_at": _iso(_now_utc())}).eq("token", token).execute()
    except Exception:
        return
