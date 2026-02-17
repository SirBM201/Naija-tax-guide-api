# app/services/credit_ledger_service.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from ..core.supabase_client import supabase


def _rows(resp: Any):
    data = getattr(resp, "data", None)
    if data is not None:
        return data
    if isinstance(resp, dict):
        return resp.get("data") or []
    return []


def get_latest_credit_row(account_id: str) -> Optional[Dict[str, Any]]:
    """
    ai_credit_ledger schema (from your screenshot):
      account_id, credits_total, credits_remaining, daily_answers_limit (nullable),
      daily_answers_used, daily_day (date), updated_at (timestamptz)
    We return the most recent row by daily_day then updated_at.
    """
    sb = supabase()
    resp = (
        sb.table("ai_credit_ledger")
        .select("*")
        .eq("account_id", account_id)
        .order("daily_day", desc=True)
        .order("updated_at", desc=True)
        .limit(1)
        .execute()
    )
    rows = _rows(resp)
    return rows[0] if rows else None
