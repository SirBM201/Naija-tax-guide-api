# app/services/web_sessions_service.py
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from ..core.config import (
    ACCESS_TOKEN_TTL_SECONDS,
    WEB_TOKEN_PEPPER,
    WEB_TOKEN_TABLE,
    WEB_TOKEN_COL_TOKEN,
    WEB_TOKEN_COL_ACCOUNT_ID,
    WEB_TOKEN_COL_EXPIRES_AT,
    WEB_TOKEN_COL_REVOKED_AT,
)
from ..core.supabase_client import supabase


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _token_hash(token: str) -> str:
    """
    Hash token with pepper so DB never stores raw token.
    """
    pepper = (WEB_TOKEN_PEPPER or "").strip()
    return _sha256_hex(f"{pepper}:{token}")


def _rows(resp: Any):
    """
    supabase-py response adapter
    """
    if resp is None:
        return []
    data = getattr(resp, "data", None)
    if data is not None:
        return data
    if isinstance(resp, dict):
        return resp.get("data") or []
    return []


def create_web_session(account_id: str, ip: Optional[str] = None, user_agent: Optional[str] = None) -> Dict[str, Any]:
    """
    Returns: { token, expires_at }
    """
    token = secrets.token_urlsafe(32)
    token_hash = _token_hash(token)

    expires_at = _now_utc() + timedelta(seconds=int(ACCESS_TOKEN_TTL_SECONDS or 2592000))
    payload = {
        WEB_TOKEN_COL_ACCOUNT_ID: account_id,
        WEB_TOKEN_COL_TOKEN: token_hash,
        WEB_TOKEN_COL_EXPIRES_AT: _iso(expires_at),
        # optional columns (present in your schema)
        "ip": ip,
        "user_agent": user_agent,
    }

    sb = supabase()
    sb.table(WEB_TOKEN_TABLE).insert(payload).execute()

    return {"token": token, "expires_at": _iso(expires_at)}


def validate_web_session(token: str) -> Tuple[bool, Optional[str], str]:
    """
    Returns: (ok, account_id, reason)
    reason: missing_token | invalid_token | revoked | expired | db_error
    """
    if not token:
        return False, None, "missing_token"

    sb = supabase()
    th = _token_hash(token)

    try:
        resp = (
            sb.table(WEB_TOKEN_TABLE)
            .select(f"{WEB_TOKEN_COL_ACCOUNT_ID},{WEB_TOKEN_COL_EXPIRES_AT},{WEB_TOKEN_COL_REVOKED_AT}")
            .eq(WEB_TOKEN_COL_TOKEN, th)
            .limit(1)
            .execute()
        )
        rows = _rows(resp)
        if not rows:
            return False, None, "invalid_token"

        row = rows[0]
        if row.get(WEB_TOKEN_COL_REVOKED_AT):
            return False, None, "revoked"

        exp = row.get(WEB_TOKEN_COL_EXPIRES_AT)
        if not exp:
            return False, None, "expired"

        # parse iso
        try:
            exp_dt = datetime.fromisoformat(str(exp).replace("Z", "+00:00"))
        except Exception:
            return False, None, "expired"

        if exp_dt <= _now_utc():
            return False, None, "expired"

        return True, row.get(WEB_TOKEN_COL_ACCOUNT_ID), "ok"
    except Exception:
        return False, None, "db_error"


def touch_session_best_effort(token: str) -> None:
    """
    Update last_seen_at; ignore failures.
    """
    if not token:
        return
    sb = supabase()
    th = _token_hash(token)
    try:
        sb.table(WEB_TOKEN_TABLE).update({"last_seen_at": _iso(_now_utc())}).eq(WEB_TOKEN_COL_TOKEN, th).execute()
    except Exception:
        return


def revoke_session(token: str) -> bool:
    """
    Marks the session revoked_at = now().

    Returns True if any row updated.
    """
    if not token:
        return False
    sb = supabase()
    th = _token_hash(token)
    try:
        resp = (
            sb.table(WEB_TOKEN_TABLE)
            .update({WEB_TOKEN_COL_REVOKED_AT: _iso(_now_utc())})
            .eq(WEB_TOKEN_COL_TOKEN, th)
            .execute()
        )
        rows = _rows(resp)
        return bool(rows)
    except Exception:
        return False
