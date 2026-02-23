# app/services/subscriptions_service.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from ..core.supabase_client import supabase

# Keep plan durations centralized.
_PLAN_DAYS: Dict[str, int] = {
    "monthly": 30,
    "quarterly": 90,
    "yearly": 365,
}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _norm_plan(plan_code: Optional[str]) -> str:
    return (plan_code or "").strip().lower()


def _duration_days(plan_code: str) -> int:
    return _PLAN_DAYS.get(plan_code, 30)


def _safe_err(e: Exception, where: str) -> Dict[str, Any]:
    return {
        "where": where,
        "type": type(e).__name__,
        "message": str(e),
    }


# -----------------------------------------------------------------------------
# Compatibility shim (IMPORTANT)
# -----------------------------------------------------------------------------
# Your routes import get_subscription_status from this module.
# If the real implementation lives elsewhere, re-export it safely.
def get_subscription_status(account_id: str) -> Dict[str, Any]:
    """
    Compatibility shim:
    - Primary: delegate to app.services.subscription_status_service.get_subscription_status
    - Fallback: compute from user_subscriptions row (never crash boot)
    """
    account_id = (account_id or "").strip()
    if not account_id:
        return {"ok": False, "error": "missing_account_id"}

    # Try the newer module first (no boot crash if missing).
    try:
        from .subscription_status_service import get_subscription_status as _gss  # type: ignore
        return _gss(account_id)
    except Exception:
        # Fallback: conservative status based on DB row
        ok, row, err = _get_user_subscription(account_id)
        if not ok:
            return {"ok": False, "error": "db_read_failed", "root_cause": err}

        if not row:
            return {
                "ok": True,
                "account_id": account_id,
                "status": "free",
                "plan_code": None,
                "active": False,
                "source": "fallback:user_subscriptions(empty)",
            }

        # Minimal interpretation
        status = (row.get("status") or "").strip().lower()
        expires_at = row.get("expires_at")
        active = status == "active"
        return {
            "ok": True,
            "account_id": account_id,
            "status": status or "unknown",
            "plan_code": row.get("plan_code"),
            "expires_at": expires_at,
            "active": active,
            "source": "fallback:user_subscriptions",
        }


# -----------------------------------------------------------------------------
# Core DB helpers
# -----------------------------------------------------------------------------
def _upsert_user_subscription(
    payload: Dict[str, Any],
) -> Tuple[bool, Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """
    Upsert into user_subscriptions using UNIQUE(account_id).
    Returns: (ok, row, error_info)
    """
    try:
        db = supabase()  # IMPORTANT: supabase is a function in this project
        res = (
            db.table("user_subscriptions")
            .upsert(payload, on_conflict="account_id")
            .select("account_id, plan_code, status, expires_at, grace_until, trial_until, created_at, updated_at")
            .execute()
        )
        rows = getattr(res, "data", None) or []
        row = rows[0] if rows else None
        return True, row, None
    except Exception as e:
        return False, None, _safe_err(e, where="user_subscriptions.upsert")


def _get_user_subscription(
    account_id: str,
) -> Tuple[bool, Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    try:
        db = supabase()
        res = (
            db.table("user_subscriptions")
            .select("account_id, plan_code, status, expires_at, grace_until, trial_until, created_at, updated_at")
            .eq("account_id", account_id)
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        row = rows[0] if rows else None
        return True, row, None
    except Exception as e:
        return False, None, _safe_err(e, where="user_subscriptions.select")


# -----------------------------------------------------------------------------
# Public service functions (used by routes)
# -----------------------------------------------------------------------------
def activate_subscription_now(
    *,
    account_id: str,
    plan_code: str,
    days: Optional[int] = None,
    status: str = "active",
) -> Dict[str, Any]:
    """
    Admin/manual activation (or internal activation after payment).

    Writes to: user_subscriptions
    Uniqueness: account_id (one row per account)
    """
    account_id = (account_id or "").strip()
    plan_code = _norm_plan(plan_code)

    if not account_id:
        return {"ok": False, "error": "missing_account_id", "root_cause": {"message": "account_id is required"}}
    if not plan_code:
        return {"ok": False, "error": "missing_plan_code", "root_cause": {"message": "plan_code is required"}}

    now = _now_utc()
    dur = int(days) if days is not None else _duration_days(plan_code)
    expires_at = now + timedelta(days=dur)

    payload = {
        "account_id": account_id,
        "plan_code": plan_code,
        "status": (status or "active").strip().lower(),
        "expires_at": _iso(expires_at),
        "grace_until": None,
        "trial_until": None,
        "updated_at": _iso(now),
    }

    ok, row, err = _upsert_user_subscription(payload)
    if not ok:
        return {
            "ok": False,
            "error": "db_upsert_failed",
            "root_cause": err,
            "where": "activate_subscription_now",
            "table": "user_subscriptions",
        }

    return {"ok": True, "account_id": account_id, "subscription": row, "table": "user_subscriptions"}


def cancel_subscription(
    *,
    account_id: str,
    status: str = "canceled",
) -> Dict[str, Any]:
    """
    Cancel but keep row for audit.
    """
    account_id = (account_id or "").strip()
    if not account_id:
        return {"ok": False, "error": "missing_account_id"}

    now = _now_utc()
    payload = {
        "account_id": account_id,
        "status": (status or "canceled").strip().lower(),
        "updated_at": _iso(now),
    }

    ok, row, err = _upsert_user_subscription(payload)
    if not ok:
        return {"ok": False, "error": "db_upsert_failed", "root_cause": err, "where": "cancel_subscription"}

    return {"ok": True, "account_id": account_id, "subscription": row}


def set_trial(
    *,
    account_id: str,
    plan_code: str = "trial",
    trial_days: int = 7,
) -> Dict[str, Any]:
    account_id = (account_id or "").strip()
    if not account_id:
        return {"ok": False, "error": "missing_account_id"}

    now = _now_utc()
    trial_until = now + timedelta(days=int(trial_days))

    payload = {
        "account_id": account_id,
        "plan_code": _norm_plan(plan_code) or "trial",
        "status": "active",
        "trial_until": _iso(trial_until),
        "updated_at": _iso(now),
    }

    ok, row, err = _upsert_user_subscription(payload)
    if not ok:
        return {"ok": False, "error": "db_upsert_failed", "root_cause": err, "where": "set_trial"}

    return {"ok": True, "account_id": account_id, "subscription": row}


def debug_read_subscription(account_id: str) -> Dict[str, Any]:
    """
    Optional helper for your debug route.
    """
    account_id = (account_id or "").strip()
    if not account_id:
        return {"ok": False, "error": "missing_account_id"}

    ok, row, err = _get_user_subscription(account_id)
    if not ok:
        return {"ok": False, "error": "db_read_failed", "root_cause": err, "where": "debug_read_subscription"}

    return {"ok": True, "account_id": account_id, "subscription": row, "table": "user_subscriptions"}
