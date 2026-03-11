from __future__ import annotations

from typing import Any, Dict, Optional

from app.core.supabase_client import supabase


def _sb():
    return supabase() if callable(supabase) else supabase


def get_monthly_ai_usage(account_id: str) -> int:
    sb = _sb()
    try:
        res = sb.rpc("get_monthly_ai_usage", {"p_account_id": account_id}).execute()
        rows = res.data or []
        if not rows:
            return 0
        row = rows[0] or {}
        return int(row.get("ai_usage_count") or 0)
    except Exception:
        return 0


def get_account_monthly_ai_limit(account_id: str) -> Dict[str, Any]:
    sb = _sb()
    try:
        res = sb.rpc("get_account_monthly_ai_limit", {"p_account_id": account_id}).execute()
        rows = res.data or []
        if not rows:
            return {"account_id": account_id, "plan_code": "monthly", "monthly_ai_limit": 200}
        row = rows[0] or {}
        return {
            "account_id": row.get("account_id") or account_id,
            "plan_code": row.get("plan_code") or "monthly",
            "monthly_ai_limit": int(row.get("monthly_ai_limit") or 200),
        }
    except Exception:
        return {"account_id": account_id, "plan_code": "monthly", "monthly_ai_limit": 200}
