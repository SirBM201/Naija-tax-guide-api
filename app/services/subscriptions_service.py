# app/services/subscriptions_service.py
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from ..core.supabase_client import supabase
from .subscription_status_service import get_subscription_status as _get_status


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if not dt:
        return None
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _subscriptions_table() -> str:
    return (os.getenv("SUBSCRIPTIONS_TABLE", "") or "").strip() or "user_subscriptions"


def get_subscription_status(account_id: str) -> Dict[str, Any]:
    # Single source of truth
    return _get_status(account_id)


def _default_expiry_for_plan(plan_code: str) -> Optional[datetime]:
    plan = (plan_code or "").strip().lower()
    now = _now_utc()

    if plan in {"trial"}:
        return now + timedelta(days=7)

    if plan in {"monthly", "month"}:
        return now + timedelta(days=30)

    if plan in {"quarterly", "quarter"}:
        return now + timedelta(days=90)

    if plan in {"yearly", "annual", "year"}:
        return now + timedelta(days=365)

    # manual: no expiry unless provided
    return None


def activate_subscription_now(
    *,
    account_id: str,
    plan_code: str = "manual",
    status: str = "active",
    expires_at_iso: Optional[str] = None,
    grace_until_iso: Optional[str] = None,
    trial_until_iso: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Admin/testing helper: upserts the canonical row in public.user_subscriptions.

    Requires that public.user_subscriptions exists with:
      account_id, plan_code, status, expires_at, grace_until, trial_until
    """
    account_id = (account_id or "").strip()
    if not account_id:
        return {"ok": False, "error": "missing_account_id"}

    plan_code = (plan_code or "manual").strip()
    status = (status or "active").strip()

    # compute expires_at if not provided (for common plans)
    expires_dt = None
    if expires_at_iso:
        try:
            expires_dt = datetime.fromisoformat(str(expires_at_iso).replace("Z", "+00:00"))
        except Exception:
            return {"ok": False, "error": "invalid_expires_at", "message": "expires_at must be ISO8601"}
    else:
        expires_dt = _default_expiry_for_plan(plan_code)

    # grace/trial parsing (optional)
    def _parse_optional(v: Optional[str], field: str) -> Optional[datetime]:
        if not v:
            return None
        try:
            return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        except Exception:
            raise ValueError(field)

    try:
        grace_dt = _parse_optional(grace_until_iso, "grace_until")
        trial_dt = _parse_optional(trial_until_iso, "trial_until")
    except ValueError as ve:
        return {"ok": False, "error": f"invalid_{str(ve)}", "message": f"{ve} must be ISO8601"}

    payload = {
        "account_id": account_id,
        "plan_code": plan_code,
        "status": status,
        "expires_at": _iso(expires_dt),
        "grace_until": _iso(grace_dt),
        "trial_until": _iso(trial_dt),
        "updated_at": _iso(_now_utc()),
    }

    table = _subscriptions_table()
    try:
        db = supabase()
        # Upsert by unique(account_id)
        res = db.table(table).upsert(payload, on_conflict="account_id").execute()
        _ = getattr(res, "data", None)
        return {"ok": True, "table": table, "account_id": account_id, "written": payload}
    except Exception as e:
        return {
            "ok": False,
            "error": "db_insert_failed",
            "message": str(e),
            "table": table,
        }


# -------------------------------------------------------------------
# Webhook handlers (MUST exist because app.routes.webhooks imports them)
# -------------------------------------------------------------------

def handle_payment_success(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Called by Paystack webhook route.

    Expected shapes vary; we keep this safe + defensive and only act if we can
    resolve account_id + plan_code.
    """
    try:
        data = (event or {}).get("data") or {}
        metadata = data.get("metadata") or {}

        # You can pass account_id via Paystack metadata when initializing payment
        account_id = (metadata.get("account_id") or metadata.get("user_id") or "").strip()
        plan_code = (metadata.get("plan_code") or metadata.get("plan") or "monthly").strip()

        if not account_id:
            return {"ok": False, "error": "missing_account_id_in_metadata"}

        # Activate (or extend) subscription
        return activate_subscription_now(account_id=account_id, plan_code=plan_code, status="active")
    except Exception as e:
        return {"ok": False, "error": "exception", "message": str(e)[:300]}


def handle_subscription_created(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Optional: Paystack subscription.create event.
    You can wire this later; for now we keep a safe no-op unless metadata provides account_id.
    """
    try:
        data = (event or {}).get("data") or {}
        metadata = data.get("metadata") or {}

        account_id = (metadata.get("account_id") or metadata.get("user_id") or "").strip()
        plan_code = (metadata.get("plan_code") or metadata.get("plan") or "monthly").strip()

        if not account_id:
            return {"ok": True, "noop": True, "reason": "no_account_id_in_metadata"}

        return activate_subscription_now(account_id=account_id, plan_code=plan_code, status="active")
    except Exception as e:
        return {"ok": False, "error": "exception", "message": str(e)[:300]}
