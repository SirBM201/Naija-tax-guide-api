from __future__ import annotations

from typing import Dict, Any

from app.core.supabase_client import supabase


def _sb():
    return supabase() if callable(supabase) else supabase


def get_billing_state(account_id: str) -> Dict[str, Any]:
    sb = _sb()

    try:
        res = sb.table("subscriptions").select("*").eq("account_id", account_id).limit(1).execute()
        row = (res.data or [{}])[0]
    except Exception:
        row = {}

    status = str(row.get("status") or "").lower()
    is_active = status == "active"

    return {
        "account_id": account_id,
        "subscription_status": status or "unknown",
        "is_active": is_active,
        "plan_code": row.get("plan_code") or "monthly",
        "expires_at": row.get("expires_at"),
    }
