# app/services/subscriptions_service.py
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from app.core.supabase_client import supabase

SUBSCRIPTIONS_TABLE = (os.getenv("SUBSCRIPTIONS_TABLE", "") or "user_subscriptions").strip()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _plan_duration(plan_code: str) -> timedelta:
    pc = (plan_code or "").strip().lower()
    # Adjust if you use different plan codes
    if pc in ("monthly", "month"):
        return timedelta(days=30)
    if pc in ("quarterly", "quarter"):
        return timedelta(days=90)
    if pc in ("yearly", "annual", "year"):
        return timedelta(days=365)
    # default: monthly
    return timedelta(days=30)


def _safe_row(res) -> Optional[Dict[str, Any]]:
    data = getattr(res, "data", None)
    if isinstance(data, list) and data:
        return data[0]
    if isinstance(data, dict):
        return data
    return None


def get_subscription_status(account_id: str) -> Dict[str, Any]:
    """
    Canonical subscription status lookup used by routes + debug.
    Returns a stable dict the frontend can consume.
    """
    account_id = (account_id or "").strip()
    if not account_id:
        return {
            "account_id": "",
            "active": False,
            "state": "none",
            "reason": "missing_account_id",
            "plan_code": None,
            "expires_at": None,
            "grace_until": None,
            "trial_until": None,
        }

    try:
        res = (
            supabase.table(SUBSCRIPTIONS_TABLE)
            .select("account_id, plan_code, status, expires_at, grace_until, trial_until, updated_at, created_at")
            .eq("account_id", account_id)
            .limit(1)
            .execute()
        )
        row = _safe_row(res)
    except Exception as e:
        return {
            "account_id": account_id,
            "active": False,
            "state": "none",
            "reason": "db_error",
            "error": str(e),
            "plan_code": None,
            "expires_at": None,
            "grace_until": None,
            "trial_until": None,
        }

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

    # Determine activeness based on expires_at (+ optional grace)
    expires_at = row.get("expires_at")
    grace_until = row.get("grace_until")
    trial_until = row.get("trial_until")
    plan_code = row.get("plan_code")
    status = (row.get("status") or "").lower().strip()

    now = _utcnow()

    def _parse_ts(v):
        # supabase often returns ISO strings; allow passthrough if already datetime-like
        if not v:
            return None
        if isinstance(v, datetime):
            return v
        if isinstance(v, str):
            try:
                # fromisoformat doesn’t like Z in some py versions; normalize
                vv = v.replace("Z", "+00:00")
                return datetime.fromisoformat(vv)
            except Exception:
                return None
        return None

    exp_dt = _parse_ts(expires_at)
    grace_dt = _parse_ts(grace_until)
    trial_dt = _parse_ts(trial_until)

    effective_until = None
    if exp_dt and grace_dt:
        effective_until = max(exp_dt, grace_dt)
    elif exp_dt:
        effective_until = exp_dt
    elif grace_dt:
        effective_until = grace_dt

    active = False
    state = "none"
    reason = "unknown"

    if status in ("active", "paid"):
        if effective_until and effective_until > now:
            active = True
            state = "active"
            reason = "within_expiry"
        else:
            active = False
            state = "expired"
            reason = "past_expiry"
    else:
        # fall back: if expiry exists and still in future, treat active
        if effective_until and effective_until > now:
            active = True
            state = "active"
            reason = "expiry_future"
        else:
            active = False
            state = "none"
            reason = "status_not_active"

    # Trial can be considered active too (optional)
    if not active and trial_dt and trial_dt > now:
        active = True
        state = "trial"
        reason = "trial_active"

    return {
        "account_id": account_id,
        "active": active,
        "state": state,
        "reason": reason,
        "plan_code": plan_code,
        "expires_at": expires_at,
        "grace_until": grace_until,
        "trial_until": trial_until,
    }


def activate_subscription_now(account_id: str, plan_code: str) -> Dict[str, Any]:
    """
    Admin/test activation: creates/updates subscription row for the account.
    IMPORTANT: always writes expires_at (prevents NOT NULL failures).
    """
    account_id = (account_id or "").strip()
    plan_code = (plan_code or "").strip().lower()
    if not account_id or not plan_code:
        return {"ok": False, "error": "missing_fields", "message": "account_id and plan_code are required"}

    now = _utcnow()
    expires_at = now + _plan_duration(plan_code)

    payload: Dict[str, Any] = {
        "account_id": account_id,
        "plan_code": plan_code,
        "status": "active",
        "expires_at": expires_at.isoformat(),
        "updated_at": now.isoformat(),
    }

    # If you added grace_until in DB, you may set it. If not, leave it out.
    # payload["grace_until"] = None

    try:
        # Upsert by account_id (requires UNIQUE(account_id))
        res = (
            supabase.table(SUBSCRIPTIONS_TABLE)
            .upsert(payload, on_conflict="account_id")
            .execute()
        )
        row = _safe_row(res)
        return {"ok": True, "subscription": row, "computed_status": get_subscription_status(account_id)}
    except Exception as e:
        return {"ok": False, "error": "db_insert_failed", "message": str(e), "table": SUBSCRIPTIONS_TABLE}


def handle_payment_success(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Called by Paystack webhook after charge.success.
    Expects: account_id, plan_code, reference, provider, etc.
    """
    account_id = (event.get("account_id") or "").strip()
    plan_code = (event.get("plan_code") or "").strip().lower()
    if not account_id or not plan_code:
        return {"ok": False, "error": "missing_fields", "message": "account_id and plan_code are required"}

    # For now: apply immediately. (You can later support upgrade_mode=at_expiry)
    out = activate_subscription_now(account_id, plan_code)
    if not out.get("ok"):
        return out

    return {
        "ok": True,
        "applied": "now",
        "account_id": account_id,
        "plan_code": plan_code,
        "reference": event.get("reference"),
        "provider": event.get("provider", "paystack"),
        "computed_status": out.get("computed_status"),
    }
