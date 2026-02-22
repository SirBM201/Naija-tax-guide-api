# app/services/subscriptions_service.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from app.core.supabase_client import supabase

# Global-standard grace window (common SaaS behavior)
GRACE_DAYS = int(("3").strip())

# If your DB has a "plans" table, we will use it.
# Otherwise, we fallback to these defaults.
FALLBACK_PLAN_DAYS = {
    "monthly": 30,
    "quarterly": 90,
    "yearly": 365,
    "trial": 7,     # you can adjust
    "manual": 30,   # admin-created manual plan
}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(v: Any) -> Optional[datetime]:
    if not v:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, str):
        # Supabase often returns ISO strings; normalize Z
        s = v.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None
    return None


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if not dt:
        return None
    return dt.astimezone(timezone.utc).isoformat()


def _compute_grace(end_at: Optional[datetime]) -> Optional[datetime]:
    if not end_at:
        return None
    return end_at + timedelta(days=GRACE_DAYS)


def _derive_access(status: str, end_at: Optional[datetime]) -> Dict[str, Any]:
    """
    Global standard:
      - active: allowed if within paid period
      - trial: allowed if within trial period (TRIAL IS ACTIVE)
      - cancelled: allowed until end_at (you already paid)
      - past_due: allowed ONLY within grace window (configurable)
      - expired: not allowed
    """
    now = _now_utc()
    grace_until = _compute_grace(end_at)

    within_period = bool(end_at and end_at > now)
    within_grace = bool(grace_until and grace_until > now)

    status_norm = (status or "").strip().lower()

    active = False
    reason = None

    if status_norm in {"active", "trial"}:
        active = within_period
        if not active:
            reason = "expired_period"

    elif status_norm == "cancelled":
        active = within_period
        if not active:
            reason = "cancelled_and_expired"

    elif status_norm == "past_due":
        active = within_grace
        if not active:
            reason = "past_due_out_of_grace"

    else:
        active = False
        reason = status_norm or "unknown_status"

    return {
        "active": active,
        "state": status_norm or "none",
        "reason": None if active else reason,
        "grace_until": grace_until,
    }


def _get_latest_subscription_row(user_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Returns (row, error_reason)
    """
    try:
        res = (
            supabase.table("subscriptions")
            .select("id, user_id, plan, status, start_at, end_at, paystack_ref, amount_kobo")
            .eq("user_id", user_id)
            .order("end_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = (res.data or []) if hasattr(res, "data") else []
        if not rows:
            return None, "no_subscription"
        return rows[0], None
    except Exception:
        return None, "status_lookup_failed"


def get_subscription_status(user_id: Optional[str]) -> Dict[str, Any]:
    """
    Public shape used by:
      - /api/subscription/status
      - /api/web/ask gating

    IMPORTANT:
      Your logs show: account_id is present in /me and verify response.
      We treat that account_id as user_id for subscriptions table.
    """
    if not user_id:
        return {
            "account_id": None,
            "active": False,
            "expires_at": None,
            "grace_until": None,
            "plan_code": None,
            "plan_expiry": None,
            "reason": "no_user",
            "state": "none",
            "debug": {"stage": "subscription_checked"},
        }

    row, err = _get_latest_subscription_row(user_id)
    if not row:
        return {
            "account_id": user_id,
            "active": False,
            "expires_at": None,
            "grace_until": None,
            "plan_code": None,
            "plan_expiry": None,
            "reason": err or "no_subscription",
            "state": "none",
            "debug": {"stage": "subscription_checked"},
        }

    status = (row.get("status") or "").strip()
    plan = row.get("plan")
    end_at = _parse_dt(row.get("end_at"))

    derived = _derive_access(status, end_at)

    return {
        "account_id": user_id,
        "active": bool(derived["active"]),
        "expires_at": _iso(end_at),
        "grace_until": _iso(derived["grace_until"]),
        "plan_code": plan,
        "plan_expiry": _iso(end_at),
        "reason": derived["reason"],
        "state": derived["state"],
        "debug": {
            "stage": "subscription_checked",
            "row_id": row.get("id"),
            "raw_status": status,
            "raw_plan": plan,
        },
    }


def _get_plan_days_from_db(plan_code: str) -> Optional[int]:
    """
    Optional: reads your plans table if present.
    Expected columns: plan_code, duration_days, active
    """
    try:
        res = (
            supabase.table("plans")
            .select("plan_code, duration_days, active")
            .eq("plan_code", plan_code)
            .limit(1)
            .execute()
        )
        rows = (res.data or []) if hasattr(res, "data") else []
        if not rows:
            return None
        r = rows[0]
        if r.get("active") is False:
            return None
        dd = r.get("duration_days")
        if isinstance(dd, int) and dd > 0:
            return dd
        # sometimes stored as text
        if isinstance(dd, str) and dd.strip().isdigit():
            return int(dd.strip())
        return None
    except Exception:
        return None


def activate_subscription_now(
    user_id: str,
    plan_code: str,
    expires_at_iso: Optional[str] = None,
    status: str = "active",
    paystack_ref: Optional[str] = None,
    amount_kobo: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Admin/testing helper:
      - Inserts a NEW row in subscriptions (history-preserving, global standard)
      - Sets start_at=now
      - Sets end_at based on plan duration or expires_at_iso if provided
    """
    if not user_id:
        return {"ok": False, "error": "no_user_id"}

    plan_code = (plan_code or "").strip().lower() or "manual"
    status = (status or "").strip().lower() or "active"

    now = _now_utc()

    if expires_at_iso:
        end_at = _parse_dt(expires_at_iso)
        if not end_at:
            return {"ok": False, "error": "invalid_expires_at"}
    else:
        days = _get_plan_days_from_db(plan_code)
        if not days:
            days = FALLBACK_PLAN_DAYS.get(plan_code, 30)
        end_at = now + timedelta(days=int(days))

    payload = {
        "user_id": user_id,
        "plan": plan_code,
        "status": status,
        "start_at": now.isoformat(),
        "end_at": end_at.isoformat(),
        "paystack_ref": paystack_ref,
        "amount_kobo": amount_kobo,
    }

    try:
        ins = supabase.table("subscriptions").insert(payload).execute()
        inserted = (ins.data or [None])[0] if hasattr(ins, "data") else None

        return {
            "ok": True,
            "inserted": inserted,
            "subscription": get_subscription_status(user_id),
        }
    except Exception:
        return {"ok": False, "error": "insert_failed"}
