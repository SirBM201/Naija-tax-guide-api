from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.core.supabase_client import supabase


# ---------------------------------------------------------
# Internal helper
# ---------------------------------------------------------

def _sb():
    """
    Supports both:
      supabase  (instance)
      supabase() (factory)
    """
    return supabase() if callable(supabase) else supabase


# ---------------------------------------------------------
# List plans
# ---------------------------------------------------------

def list_plans(active_only: bool = True) -> List[Dict[str, Any]]:
    """
    Reads plans from Supabase table 'plans'.

    Safe fallback:
      - If table missing
      - If query fails
      → returns default plans so UI never breaks.
    """
    try:
        q = _sb().table("plans").select(
            "plan_code,name,duration_days,active,price,created_at"
        )

        if active_only:
            q = q.eq("active", True)

        res = q.order("duration_days").execute()

        rows = getattr(res, "data", None) or []
        if rows:
            return rows

    except Exception:
        pass

    # -------------------------
    # Safe fallback defaults
    # -------------------------
    return [
        {
            "plan_code": "monthly",
            "name": "Monthly Plan",
            "duration_days": 30,
            "price": 3000,
            "active": True,
        },
        {
            "plan_code": "quarterly",
            "name": "Quarterly Plan",
            "duration_days": 90,
            "price": 8000,
            "active": True,
        },
        {
            "plan_code": "yearly",
            "name": "Yearly Plan",
            "duration_days": 365,
            "price": 30000,
            "active": True,
        },
    ]


# ---------------------------------------------------------
# Single plan
# ---------------------------------------------------------

def get_plan(plan_code: str) -> Optional[Dict[str, Any]]:
    plan_code = (plan_code or "").strip()
    if not plan_code:
        return None

    try:
        res = (
            _sb()
            .table("plans")
            .select(
                "plan_code,name,duration_days,active,price,created_at"
            )
            .eq("plan_code", plan_code)
            .limit(1)
            .execute()
        )

        rows = getattr(res, "data", None) or []
        if rows:
            return rows[0]

    except Exception:
        pass

    # fallback search in defaults
    defaults = list_plans(active_only=False)
    for p in defaults:
        if p["plan_code"] == plan_code:
            return p

    return None
