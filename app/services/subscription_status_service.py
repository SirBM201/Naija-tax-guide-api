# app/services/subscription_status_service.py
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

from ..core.supabase_client import supabase


# -------------------------------------------------------------------
# Time helpers
# -------------------------------------------------------------------

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        v = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(v)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if not dt:
        return None
    if not dt.tzinfo:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


# -------------------------------------------------------------------
# DB helper (supabase in this repo is a FACTORY FUNCTION)
# -------------------------------------------------------------------

def _db():
    return supabase() if callable(supabase) else supabase


# -------------------------------------------------------------------
# Public API
# -------------------------------------------------------------------

def get_subscription_status(account_id: Optional[str]) -> Dict[str, Any]:
    """
    Source of truth: public.user_subscriptions (latest row by created_at)

    Returns:
      {
        ok: bool,
        account_id: str|null,
        active: bool,
        state: "none"|"trialing"|"active"|"grace"|"expired"|"canceled"|"error",
        plan_code: str|null,
        expires_at: str|null,
        grace_until: str|null,
        reason: str,
        message: str|null
      }
    """
    account_id = (account_id or "").strip()
    if not account_id:
        return {
            "ok": True,
            "account_id": None,
            "active": False,
            "state": "none",
            "plan_code": None,
            "expires_at": None,
            "grace_until": None,
            "reason": "no_account_id",
            "message": None,
        }

    # Fetch latest subscription row
    try:
        res = (
            _db()
            .table("user_subscriptions")
            .select("*")
            .eq("account_id", account_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        row = rows[0] if rows else None
    except Exception as e:
        return {
            "ok": False,
            "account_id": account_id,
            "active": False,
            "state": "error",
            "plan_code": None,
            "expires_at": None,
            "grace_until": None,
            "reason": "db_error",
            "message": repr(e),
        }

    if not row:
        return {
            "ok": True,
            "account_id": account_id,
            "active": False,
            "state": "none",
            "plan_code": None,
            "expires_at": None,
            "grace_until": None,
            "reason": "no_subscription",
            "message": None,
        }

    # Normalize
    plan_code = (row.get("plan_code") or row.get("plan") or None)
    status = str(row.get("status") or "").strip().lower()  # optional column
    expires_at = row.get("expires_at")
    grace_until = row.get("grace_until")

    now = _now_utc()
    exp_dt = _parse_iso(expires_at)
    grace_dt = _parse_iso(grace_until)

    # If your table stores trials but doesn't store expires_at, compute from created_at (best-effort)
    if (status in {"trial", "trialing"} or str(plan_code or "").lower() == "trial") and not exp_dt:
        created_dt = _parse_iso(row.get("created_at"))
        if created_dt:
            exp_dt = created_dt + timedelta(days=7)  # default trial window; keep simple here
            expires_at = _iso(exp_dt)

    # Canceled (only if status column exists and is used)
    if status in {"canceled", "cancelled"}:
        return {
            "ok": True,
            "account_id": account_id,
            "active": False,
            "state": "canceled",
            "plan_code": plan_code,
            "expires_at": expires_at,
            "grace_until": grace_until,
            "reason": "canceled",
            "message": None,
        }

    # Trial counts as active while within expiry window (global-standard)
    if status in {"trial", "trialing"} or str(plan_code or "").lower() == "trial":
        if exp_dt and exp_dt > now:
            return {
                "ok": True,
                "account_id": account_id,
                "active": True,
                "state": "trialing",
                "plan_code": plan_code,
                "expires_at": expires_at,
                "grace_until": grace_until,
                "reason": "trial_active",
                "message": None,
            }
        # trial ended
        return {
            "ok": True,
            "account_id": account_id,
            "active": False,
            "state": "expired",
            "plan_code": plan_code,
            "expires_at": expires_at,
            "grace_until": grace_until,
            "reason": "trial_ended" if exp_dt else "trial_no_expiry",
            "message": None,
        }

    # Active window
    if exp_dt and exp_dt > now:
        return {
            "ok": True,
            "account_id": account_id,
            "active": True,
            "state": "active",
            "plan_code": plan_code,
            "expires_at": expires_at,
            "grace_until": grace_until,
            "reason": "within_expiry",
            "message": None,
        }

    # Grace window (optionally keep access)
    if grace_dt and grace_dt > now:
        return {
            "ok": True,
            "account_id": account_id,
            "active": True,
            "state": "grace",
            "plan_code": plan_code,
            "expires_at": expires_at,
            "grace_until": grace_until,
            "reason": "within_grace",
            "message": None,
        }

    # Expired
    return {
        "ok": True,
        "account_id": account_id,
        "active": False,
        "state": "expired",
        "plan_code": plan_code,
        "expires_at": expires_at,
        "grace_until": grace_until,
        "reason": "expired",
        "message": None,
    }
