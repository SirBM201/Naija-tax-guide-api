# app/services/subscriptions_service.py
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from app.core.supabase_client import supabase


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _safe_dict(v: Any) -> Dict[str, Any]:
    return v if isinstance(v, dict) else {}


# -------------------------------------------------------------------
# PUBLIC API (routes import these names)
# -------------------------------------------------------------------

def get_subscription_status(account_id: str) -> Dict[str, Any]:
    """
    Global-standard shape (stable for frontend):
      active: bool
      state:  "active" | "trial" | "grace" | "none"
      reason: machine-readable reason
      plan_code, expires_at, grace_until
    """
    try:
        res = (
            supabase.table("subscriptions")
            .select("account_id, plan_code, status, expires_at, grace_until, trial_until")
            .eq("account_id", account_id)
            .order("updated_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = (res.data or []) if hasattr(res, "data") else []
        row = rows[0] if rows else None

        now = _utcnow()

        if not row:
            return {
                "account_id": account_id,
                "active": False,
                "state": "none",
                "reason": "no_subscription",
                "plan_code": None,
                "expires_at": None,
                "grace_until": None,
                "trial_until": None,
            }

        plan_code = row.get("plan_code")
        status = (row.get("status") or "").lower()

        expires_at = _parse_dt(row.get("expires_at"))
        grace_until = _parse_dt(row.get("grace_until"))
        trial_until = _parse_dt(row.get("trial_until"))

        # Trial treated as active (global standard)
        if trial_until and trial_until > now:
            return {
                "account_id": account_id,
                "active": True,
                "state": "trial",
                "reason": "trial_active",
                "plan_code": plan_code,
                "expires_at": _iso(expires_at),
                "grace_until": _iso(grace_until),
                "trial_until": _iso(trial_until),
            }

        # Paid active
        if expires_at and expires_at > now:
            return {
                "account_id": account_id,
                "active": True,
                "state": "active",
                "reason": "paid_active",
                "plan_code": plan_code,
                "expires_at": _iso(expires_at),
                "grace_until": _iso(grace_until),
                "trial_until": _iso(trial_until),
            }

        # Grace period (optional)
        if grace_until and grace_until > now:
            return {
                "account_id": account_id,
                "active": True,
                "state": "grace",
                "reason": "in_grace",
                "plan_code": plan_code,
                "expires_at": _iso(expires_at),
                "grace_until": _iso(grace_until),
                "trial_until": _iso(trial_until),
            }

        return {
            "account_id": account_id,
            "active": False,
            "state": "none",
            "reason": "expired",
            "plan_code": plan_code,
            "expires_at": _iso(expires_at),
            "grace_until": _iso(grace_until),
            "trial_until": _iso(trial_until),
        }

    except Exception as e:
        return {
            "account_id": account_id,
            "active": False,
            "state": "none",
            "reason": "error",
            "error": repr(e),
            "plan_code": None,
            "expires_at": None,
            "grace_until": None,
            "trial_until": None,
        }


def activate_subscription_now(account_id: str, plan_code: str, *, days: Optional[int] = None) -> Dict[str, Any]:
    """
    Manual/admin activation helper.
    """
    now = _utcnow()
    if days is None:
        days = _plan_days(plan_code)

    expires_at = now + timedelta(days=days)

    payload = {
        "account_id": account_id,
        "plan_code": plan_code,
        "status": "active",
        "expires_at": _iso(expires_at),
        "updated_at": _iso(now),
    }

    res = supabase.table("subscriptions").upsert(payload).execute()
    ok = True
    if hasattr(res, "data"):
        ok = True
    return {"ok": ok, "account_id": account_id, "plan_code": plan_code, "expires_at": _iso(expires_at)}


# -------------------------------------------------------------------
# BACKWARD-COMPAT HOOKS (so routes/webhooks.py won’t crash)
# -------------------------------------------------------------------

def handle_payment_success(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    """
    Compat wrapper: accept any historical signature.
    Expect either:
      - payload dict in args[0], or
      - payload=... in kwargs
    """
    payload = _safe_dict(kwargs.get("payload"))
    if not payload and args:
        payload = _safe_dict(args[0])

    account_id, plan_code = _extract_account_and_plan(payload)

    if not account_id or not plan_code:
        return {"ok": False, "reason": "missing_account_or_plan", "account_id": account_id, "plan_code": plan_code}

    out = activate_subscription_now(account_id, plan_code)
    out["source"] = "handle_payment_success"
    return out


def handle_payment_failure(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    """
    Compat wrapper. Often you’ll log failure / mark invoice failed.
    """
    payload = _safe_dict(kwargs.get("payload"))
    if not payload and args:
        payload = _safe_dict(args[0])

    return {"ok": True, "source": "handle_payment_failure", "received": True, "keys": sorted(list(payload.keys()))}


# -------------------------------------------------------------------
# INTERNAL HELPERS
# -------------------------------------------------------------------

def _parse_dt(v: Any) -> Optional[datetime]:
    if not v:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, str):
        try:
            # Handles "2026-03-24T15:12:54.903861+00:00"
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None
    return None


def _plan_days(plan_code: str) -> int:
    p = (plan_code or "").strip().lower()
    if p in {"monthly", "month"}:
        return 30
    if p in {"quarterly", "quarter"}:
        return 90
    if p in {"yearly", "annual", "year"}:
        return 365
    # Default safe fallback
    return int(os.getenv("DEFAULT_PLAN_DAYS", "30"))


def _extract_account_and_plan(payload: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """
    Tries common webhook payload locations:
      - payload["data"]["metadata"]["account_id"]
      - payload["metadata"]["account_id"]
      - payload["account_id"]
    And plan_code from similar places.
    """
    data = _safe_dict(payload.get("data"))
    meta = _safe_dict(data.get("metadata")) or _safe_dict(payload.get("metadata"))

    account_id = meta.get("account_id") or data.get("account_id") or payload.get("account_id")
    plan_code = meta.get("plan_code") or meta.get("plan") or data.get("plan_code") or payload.get("plan_code")

    if isinstance(account_id, str):
        account_id = account_id.strip() or None
    else:
        account_id = None

    if isinstance(plan_code, str):
        plan_code = plan_code.strip() or None
    else:
        plan_code = None

    return account_id, plan_code
