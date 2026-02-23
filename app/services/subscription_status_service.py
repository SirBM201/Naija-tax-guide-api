# app/services/subscription_status_service.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.core.supabase_client import supabase  # IMPORTANT: this is a CLIENT, not a function


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        v = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(v)
    except Exception:
        return None


def _as_iso_or_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(value)


def _compute_state(
    *,
    now: datetime,
    plan_code: Optional[str],
    status: Optional[str],
    expires_at: Optional[str],
    grace_until: Optional[str],
    trial_until: Optional[str],
) -> Dict[str, Any]:
    status_norm = (status or "").strip().lower()
    exp_dt = _parse_iso(expires_at)
    grace_dt = _parse_iso(grace_until)
    trial_dt = _parse_iso(trial_until)

    explicitly_inactive = status_norm in {"canceled", "cancelled", "inactive", "disabled", "paused"}

    if trial_dt and trial_dt > now and not explicitly_inactive:
        return {
            "active": True,
            "state": "trial",
            "reason": "within_trial",
            "plan_code": plan_code,
            "expires_at": _as_iso_or_none(expires_at),
            "grace_until": _as_iso_or_none(grace_until),
            "trial_until": _as_iso_or_none(trial_until),
        }

    if exp_dt and exp_dt > now and not explicitly_inactive:
        return {
            "active": True,
            "state": "active",
            "reason": "within_expiry",
            "plan_code": plan_code,
            "expires_at": _as_iso_or_none(expires_at),
            "grace_until": _as_iso_or_none(grace_until),
            "trial_until": _as_iso_or_none(trial_until),
        }

    if grace_dt and grace_dt > now and not explicitly_inactive:
        return {
            "active": True,
            "state": "grace",
            "reason": "within_grace",
            "plan_code": plan_code,
            "expires_at": _as_iso_or_none(expires_at),
            "grace_until": _as_iso_or_none(grace_until),
            "trial_until": _as_iso_or_none(trial_until),
        }

    if exp_dt or grace_dt or trial_dt:
        return {
            "active": False,
            "state": "expired",
            "reason": "expired",
            "plan_code": plan_code,
            "expires_at": _as_iso_or_none(expires_at),
            "grace_until": _as_iso_or_none(grace_until),
            "trial_until": _as_iso_or_none(trial_until),
        }

    return {
        "active": False,
        "state": "none",
        "reason": "no_subscription",
        "plan_code": None,
        "expires_at": None,
        "grace_until": None,
        "trial_until": None,
    }


def get_subscription_status(account_id: str) -> Dict[str, Any]:
    """
    Source of truth: public.user_subscriptions
    """
    account_id = (account_id or "").strip()
    if not account_id:
        return {
            "account_id": "",
            "active": False,
            "state": "none",
            "plan_code": None,
            "expires_at": None,
            "grace_until": None,
            "trial_until": None,
            "reason": "no_account_id",
            "debug_source": {"table": "user_subscriptions"},
        }

    try:
        res = (
            supabase.table("user_subscriptions")
            .select("account_id, plan_code, status, expires_at, grace_until, trial_until, created_at, updated_at")
            .eq("account_id", account_id)
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        row = rows[0] if rows else None
    except Exception as e:
        return {
            "account_id": account_id,
            "active": False,
            "state": "none",
            "plan_code": None,
            "expires_at": None,
            "grace_until": None,
            "trial_until": None,
            "reason": "db_error",
            "debug_source": {"table": "user_subscriptions", "error": repr(e)},
        }

    if not row:
        return {
            "account_id": account_id,
            "active": False,
            "state": "none",
            "plan_code": None,
            "expires_at": None,
            "grace_until": None,
            "trial_until": None,
            "reason": "no_subscription",
            "debug_source": {"table": "user_subscriptions"},
        }

    computed = _compute_state(
        now=_now_utc(),
        plan_code=row.get("plan_code"),
        status=row.get("status"),
        expires_at=_as_iso_or_none(row.get("expires_at")),
        grace_until=_as_iso_or_none(row.get("grace_until")),
        trial_until=_as_iso_or_none(row.get("trial_until")),
    )

    return {"account_id": account_id, **computed, "debug_source": {"table": "user_subscriptions"}}
