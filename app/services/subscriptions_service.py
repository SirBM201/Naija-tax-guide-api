# app/services/subscriptions_service.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from ..core.supabase_client import supabase


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


def _to_iso(dt: Optional[datetime]) -> Optional[str]:
    if not dt:
        return None
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _default_expiry_for_plan(plan_code: str, *, now: datetime) -> Optional[datetime]:
    p = (plan_code or "").strip().lower()

    # Keep these simple and predictable for now
    if p in {"trial"}:
        return now + timedelta(days=7)
    if p in {"monthly", "month"}:
        return now + timedelta(days=30)
    if p in {"quarterly", "quarter"}:
        return now + timedelta(days=90)
    if p in {"yearly", "annual", "year"}:
        return now + timedelta(days=365)

    # manual: require expires_at to be explicitly set (or leave null)
    return None


def activate_subscription_now(
    *,
    user_id: str,
    plan_code: str = "manual",
    expires_at_iso: Optional[str] = None,
    status: str = "active",
    grace_days: int = 3,
    trial_until_iso: Optional[str] = None,
    provider: Optional[str] = "admin",
    provider_ref: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Admin/testing helper:
    Upserts a row into public.user_subscriptions using account_id.

    Requires SQL schema:
      user_subscriptions(account_id unique, plan_code, status, expires_at, grace_until, trial_until, ...)
    """
    account_id = (user_id or "").strip()
    if not account_id:
        return {"ok": False, "error": "missing_account_id"}

    now = _now_utc()

    exp_dt = _parse_iso(expires_at_iso)
    trial_dt = _parse_iso(trial_until_iso)

    if not exp_dt and plan_code:
        exp_dt = _default_expiry_for_plan(plan_code, now=now)

    grace_dt = None
    if exp_dt:
        try:
            grace_dt = exp_dt + timedelta(days=int(grace_days))
        except Exception:
            grace_dt = exp_dt + timedelta(days=3)

    payload: Dict[str, Any] = {
        "account_id": account_id,
        "plan_code": (plan_code or "").strip() or None,
        "status": (status or "").strip() or "active",
        "expires_at": _to_iso(exp_dt),
        "grace_until": _to_iso(grace_dt),
        "trial_until": _to_iso(trial_dt),
        "provider": provider,
        "provider_ref": provider_ref,
        "updated_at": _to_iso(now),
    }

    # Remove keys with None to avoid overwriting with null unintentionally
    payload = {k: v for k, v in payload.items() if v is not None}

    try:
        db = supabase()
        # Requires UNIQUE(account_id) so upsert works deterministically
        res = (
            db.table("user_subscriptions")
            .upsert(payload, on_conflict="account_id")
            .execute()
        )
        return {"ok": True, "account_id": account_id, "subscription": getattr(res, "data", None)}
    except Exception as e:
        return {"ok": False, "error": "db_upsert_failed", "message": repr(e)}


def handle_payment_success(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Webhook entrypoint (Paystack or other).
    This exists primarily so imports don't crash the app,
    and to apply a subscription update when metadata includes account_id.

    Expected places for IDs:
      event["data"]["metadata"]["account_id"] OR ["user_id"]
    Expected plan:
      event["data"]["metadata"]["plan_code"] OR ["plan"]

    If not found, we return ok=False but do not raise.
    """
    try:
        data = (event or {}).get("data") or {}
        meta = data.get("metadata") or {}

        account_id = (meta.get("account_id") or meta.get("user_id") or "").strip()
        plan_code = (meta.get("plan_code") or meta.get("plan") or "monthly").strip()

        provider_ref = str(data.get("reference") or data.get("id") or "") or None

        if not account_id:
            return {"ok": False, "error": "missing_account_id_in_metadata"}

        # For now: activate using default expiry for plan unless metadata includes expires_at
        expires_at = meta.get("expires_at")
        trial_until = meta.get("trial_until")

        return activate_subscription_now(
            user_id=account_id,
            plan_code=plan_code,
            expires_at_iso=expires_at,
            trial_until_iso=trial_until,
            status="active",
            provider="webhook",
            provider_ref=provider_ref,
        )
    except Exception as e:
        # Never crash webhook worker
        return {"ok": False, "error": "handle_payment_success_failed", "message": repr(e)}
