# app/services/plans_service.py
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.core.supabase_client import supabase

DEFAULT_PLANS: List[Dict[str, Any]] = [
    {"plan_code": "monthly", "name": "Monthly Plan", "price": 3000, "duration_days": 30, "active": True},
    {"plan_code": "quarterly", "name": "Quarterly Plan", "price": 8000, "duration_days": 90, "active": True},
    {"plan_code": "yearly", "name": "Yearly Plan", "price": 30000, "duration_days": 365, "active": True},
]


def _sb():
    return supabase() if callable(supabase) else supabase


def list_plans(active_only: bool = True) -> List[Dict[str, Any]]:
    """
    Tries DB table 'plans'. If missing, returns DEFAULT_PLANS.
    """
    try:
        q = _sb().table("plans").select("plan_code,name,price,duration_days,active")
        if active_only:
            q = q.eq("active", True)
        res = q.order("duration_days", desc=False).execute()
        rows = getattr(res, "data", None) or []
        return rows if rows else DEFAULT_PLANS
    except Exception:
        return DEFAULT_PLANS


def get_plan(plan_code: str) -> Optional[Dict[str, Any]]:
    code = (plan_code or "").strip().lower()
    if not code:
        return None

    # DB first
    try:
        res = (
            _sb()
            .table("plans")
            .select("plan_code,name,price,duration_days,active")
            .eq("plan_code", code)
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        if rows:
            return rows[0]
    except Exception:
        pass

    # fallback
    for p in DEFAULT_PLANS:
        if p["plan_code"] == code:
            return p

    return None
