# app/services/subscriptions_service.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from app.core.supabase_client import supabase


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso(dt: datetime) -> str:
    # Keep PostgREST happy with timezone-aware ISO
    return dt.astimezone(timezone.utc).isoformat()


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def _default_expiry_for_plan(plan_code: str) -> datetime:
    """
    IMPORTANT: Your DB currently requires expires_at NOT NULL.
    So we always compute one if caller doesn't provide it.

    Adjust durations anytime you want.
    """
    p = (plan_code or "").strip().lower()
    now = _now_utc()

    if p in {"monthly", "month"}:
        return now + timedelta(days=30)
    if p in {"quarterly", "quarter"}:
        return now + timedelta(days=90)
    if p in {"yearly", "annual", "year"}:
        return now + timedelta(days=365)
    if p in {"trial"}:
        # trial still needs expires_at due to DB constraint
        return now + timedelta(days=7)

    # manual/unknown => still give a safe default
    return now + timedelta(days=30)


def activate_subscription_now(
    *,
    user_id: str,
    plan_code: str,
    status: str = "active",
    expires_at_iso: Optional[str] = None,
    grace_until_iso: Optional[str] = None,
    trial_until_iso: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Upsert into public.user_subscriptions by unique(account_id).
    """
    account_id = (user_id or "").strip()
    if not account_id:
        return {"ok": False, "error": "missing_account_id"}

    plan_code = (plan_code or "manual").strip()
    status = (status or "active").strip()

    # compute expiry if not provided (DB not-null)
    exp_dt = _parse_iso(expires_at_iso) or _default_expiry_for_plan(plan_code)

    payload: Dict[str, Any] = {
        "account_id": account_id,
        "plan_code": plan_code,
        "status": status,
        "expires_at": _to_iso(exp_dt),
        "grace_until": grace_until_iso,
        "trial_until": trial_until_iso,
        "updated_at": _to_iso(_now_utc()),
    }

    try:
        # IMPORTANT: rely on unique(account_id) constraint you already created
        res = (
            supabase.table("user_subscriptions")
            .upsert(payload, on_conflict="account_id")
            .execute()
        )
        return {"ok": True, "account_id": account_id, "payload": payload, "db": getattr(res, "data", None)}
    except Exception as e:
        return {
            "ok": False,
            "error": "db_upsert_failed",
            "account_id": account_id,
            "payload": payload,
            "root_cause": repr(e),
            "table": "user_subscriptions",
        }


def handle_payment_success(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Minimal Paystack webhook handler.
    Expects:
      { account_id, plan_code, upgrade_mode, reference, amount_kobo, currency, raw, ... }
    """
    account_id = (event.get("account_id") or "").strip()
    plan_code = (event.get("plan_code") or "").strip()
    upgrade_mode = (event.get("upgrade_mode") or "now").strip().lower()

    if not account_id or not plan_code:
        return {"ok": False, "error": "missing_account_or_plan", "event": event}

    # For now: treat both "now" and "at_expiry" the same until you implement upgrade scheduling
    # (You can extend this later by reading existing expires_at then stacking time).
    res = activate_subscription_now(
        user_id=account_id,
        plan_code=plan_code,
        status="active",
    )

    if not res.get("ok"):
        return {"ok": False, "error": "subscription_update_failed", "details": res}

    # Optional: store payment log if you have a table for it (skip if not)
    # Example: payments(reference, account_id, amount_kobo, currency, provider, raw, created_at)
    # If you want, tell me your payments table schema and I’ll wire it.

    return {"ok": True, "applied": True, "upgrade_mode": upgrade_mode, "subscription": res}
