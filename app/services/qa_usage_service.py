# app/services/qa_usage_service.py
from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import Any, Dict, Optional, Tuple

from ..core.supabase_client import supabase


USAGE_TABLE = (os.getenv("QA_USAGE_TABLE", "qa_usage_daily") or "qa_usage_daily").strip()


def _truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _debug_enabled() -> bool:
    # allow either flag
    return _truthy(os.getenv("ASK_DEBUG")) or _truthy(os.getenv("WEB_AUTH_DEBUG"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _today() -> str:
    return date.today().isoformat()


def get_cache_used_today(account_id: str) -> int:
    aid = (account_id or "").strip()
    if not aid:
        return 0

    try:
        res = (
            supabase()
            .table(USAGE_TABLE)
            .select("cache_used")
            .eq("account_id", aid)
            .eq("day", _today())
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        if not rows:
            return 0
        return int(rows[0].get("cache_used") or 0)
    except Exception:
        return 0


def try_consume_cache_slot(account_id: str, daily_limit: int) -> Tuple[bool, Dict[str, Any]]:
    """
    Returns (ok, debug_meta)
    - ok=False means cache limit reached
    - debug_meta always safe (no secrets)
    """
    aid = (account_id or "").strip()
    lim = int(daily_limit or 0)
    if not aid:
        return False, {"reason": "no_account_id"}
    if lim <= 0:
        # treat <=0 as "unlimited"
        return True, {"reason": "unlimited"}

    dbg: Dict[str, Any] = {"table": USAGE_TABLE, "day": _today(), "limit": lim}

    try:
        used = get_cache_used_today(aid)
        dbg["used_before"] = used

        if used >= lim:
            dbg["reason"] = "limit_reached"
            return False, dbg

        new_used = used + 1

        # best-effort upsert with PK(account_id, day)
        supabase().table(USAGE_TABLE).upsert(
            {
                "account_id": aid,
                "day": _today(),
                "cache_used": new_used,
                "updated_at": _now_iso(),
            },
            on_conflict="account_id,day",
        ).execute()

        dbg["used_after"] = new_used
        dbg["reason"] = "consumed"
        return True, dbg

    except Exception as e:
        # if usage table missing, expose root-cause via debug (but do not crash)
        dbg["reason"] = "usage_update_failed"
        if _debug_enabled():
            dbg["error_type"] = type(e).__name__
            dbg["error"] = str(e)[:220]
        return False, dbg
