# app/services/subscriptions_service.py
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from ..core.supabase_client import supabase
from .subscription_status_service import get_subscription_status as _get_status


SUBSCRIPTIONS_TABLE = (os.getenv("SUBSCRIPTIONS_TABLE", "") or "").strip() or "user_subscriptions"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso(dt: Optional[datetime]) -> Optional[str]:
    if not dt:
        return None
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        v = str(s).replace("Z", "+00:00")
        return datetime.fromisoformat(v)
    except Exception:
        return None


def get_subscription_status(account_id: str) -> Dict[str, Any]:
    # keep one source of truth
    return _get_status(account_id)


def activate_subscription_now(
    *,
    user_id: str,
    plan_code: str = "manual",
    expires_at_iso: Optional[str] = None,
    status: str = "active",
    grace_days: int = 0,
    trial_days: int = 0,
) -> Dict[str, Any]:
    """
    ADMIN/testing helper: write a subscription row.

    We write to SUBSCRIPTIONS_TABLE (defaults to user_subscriptions).
    """
    account_id = (user_id or "").strip()
    plan_code = (plan_code or "manual").strip()
    status = (status or "active").strip()

    if not account_id:
        return {"ok": False, "error": "missing_account_id"}

    now = _now_utc()

    exp_dt = _parse_iso(expires_at_iso) if expires_at_iso else None
    if exp_dt is None:
        # default expiries by plan_code (simple + practical)
        if plan_code.lower() in {"monthly", "month"}:
            exp_dt = now + timedelta(days=30)
        elif plan_code.lower() in {"quarterly", "quarter"}:
            exp_dt = now + timedelta(days=90)
        elif plan_code.lower() in {"yearly", "annual", "year"}:
            exp_dt = now + timedelta(days=365)
        elif plan_code.lower() in {"trial"}:
            exp_dt = None  # trial can be handled via trial_until
        else:
            exp_dt = now + timedelta(days=30)

    grace_until = (exp_dt + timedelta(days=grace_days)) if (exp_dt and grace_days > 0) else None
    trial_until = (now + timedelta(days=trial_days)) if trial_days > 0 else None

    payload: Dict[str, Any] = {
        "account_id": account_id,
        "plan_code": plan_code,
        "status": status,
        "expires_at": _to_iso(exp_dt),
        "grace_until": _to_iso(grace_until),
        "trial_until": _to_iso(trial_until),
    }

    try:
        db = supabase()
        # insert a new row (history-friendly); status reader selects latest by created_at
        res = db.table(SUBSCRIPTIONS_TABLE).insert(payload).execute()
        data = getattr(res, "data", None) or []
        row = data[0] if data else None
        return {
            "ok": True,
            "source": "activate_subscription_now",
            "table": SUBSCRIPTIONS_TABLE,
            "inserted": bool(row),
            "row": row,
        }
    except Exception as e:
        return {
            "ok": False,
            "error": "db_insert_failed",
            "message": str(e)[:800],
            "table": SUBSCRIPTIONS_TABLE,
            "payload_keys": sorted(list(payload.keys())),
        }


# -------------------------------------------------------------------
# Webhook compatibility (required by app/routes/webhooks.py)
# -------------------------------------------------------------------

def handle_payment_success(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    """
    Compatibility function required by webhooks blueprint import.

    Expected kwargs (best-effort):
      - account_id (preferred) OR user_id
      - plan_code
      - expires_at / expires_at_iso (optional)
    """
    account_id = (kwargs.get("account_id") or kwargs.get("user_id") or "").strip()
    plan_code = (kwargs.get("plan_code") or kwargs.get("plan") or "monthly").strip()
    expires_at_iso = kwargs.get("expires_at_iso") or kwargs.get("expires_at")

    if not account_id:
        return {"ok": False, "error": "missing_account_id", "source": "handle_payment_success"}

    out = activate_subscription_now(
        user_id=account_id,
        plan_code=plan_code,
        expires_at_iso=expires_at_iso,
        status="active",
    )
    out["source"] = "handle_payment_success"
    return out


def handle_payment_failure(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    # Keep webhook handler non-fatal. You can expand later.
    payload = kwargs.get("payload") or {}
    return {
        "ok": True,
        "source": "handle_payment_failure",
        "received": True,
        "keys": sorted(list(payload.keys())) if isinstance(payload, dict) else [],
    }
