from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.core.supabase_client import supabase

DEFAULT_PLANS: List[Dict[str, Any]] = [
    {
        "plan_code": "monthly",
        "name": "Monthly Plan",
        "price": 3000,
        "duration_days": 30,
        "active": True,
        "ai_credits_total": 300,
        "daily_answers_limit": 20,
    },
    {
        "plan_code": "quarterly",
        "name": "Quarterly Plan",
        "price": 8000,
        "duration_days": 90,
        "active": True,
        "ai_credits_total": 900,
        "daily_answers_limit": 30,
    },
    {
        "plan_code": "yearly",
        "name": "Yearly Plan",
        "price": 30000,
        "duration_days": 365,
        "active": True,
        "ai_credits_total": 3600,
        "daily_answers_limit": 50,
    },
]


def _sb():
    return supabase() if callable(supabase) else supabase


def _normalize_plan(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "plan_code": row.get("plan_code"),
        "name": row.get("name"),
        "price": int(row.get("price") or 0),
        "duration_days": int(row.get("duration_days") or 0),
        "active": bool(row.get("active", True)),
        "ai_credits_total": int(row.get("ai_credits_total") or 0),
        "daily_answers_limit": int(row.get("daily_answers_limit") or 0),
    }


def list_plans(active_only: bool = True) -> List[Dict[str, Any]]:
    """
    Tries DB table 'plans'. If missing, returns DEFAULT_PLANS.
    """
    try:
        q = _sb().table("plans").select(
            "plan_code,name,price,duration_days,active,ai_credits_total,daily_answers_limit"
        )
        if active_only:
            q = q.eq("active", True)

        res = q.order("duration_days", desc=False).execute()
        rows = getattr(res, "data", None) or []
        if rows:
            return [_normalize_plan(r) for r in rows]
        return DEFAULT_PLANS
    except Exception:
        return DEFAULT_PLANS


def get_plan(plan_code: str) -> Optional[Dict[str, Any]]:
    code = (plan_code or "").strip().lower()
    if not code:
        return None

    try:
        res = (
            _sb()
            .table("plans")
            .select("plan_code,name,price,duration_days,active,ai_credits_total,daily_answers_limit")
            .eq("plan_code", code)
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        if rows:
            return _normalize_plan(rows[0])
    except Exception:
        pass

    for p in DEFAULT_PLANS:
        if p["plan_code"] == code:
            return p

    return None
