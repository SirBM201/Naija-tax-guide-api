# app/services/subscriptions_service.py
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.core.supabase_client import supabase  # supabase() in your project
from app.services.subscription_status_service import get_subscription_status as _get_sub_status


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _table_name() -> str:
    # canonical table for your backend
    return (os.getenv("SUBSCRIPTIONS_TABLE", "") or "user_subscriptions").strip() or "user_subscriptions"


def activate_subscription_now(
    *,
    user_id: str,
    plan_code: str = "manual",
    status: str = "active",
    expires_at_iso: Optional[str] = None,
    grace_until_iso: Optional[str] = None,
    trial_until_iso: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create OR update a subscription row for an account_id.

    Requires DB columns (recommended):
      account_id uuid not null
      plan_code text
      status text
      expires_at timestamptz
      grace_until timestamptz
      trial_until timestamptz
      updated_at timestamptz
      created_at timestamptz (optional, default now())
    """
    account_id = (user_id or "").strip()
    if not account_id:
        return {"ok": False, "error": "missing_account_id"}

    table = _table_name()

    payload: Dict[str, Any] = {
        "account_id": account_id,
        "plan_code": (plan_code or "manual").strip(),
        "status": (status or "active").strip(),
        "updated_at": _now_iso(),
    }

    # only include optional timestamps if provided
    if expires_at_iso:
        payload["expires_at"] = expires_at_iso
    if grace_until_iso:
        payload["grace_until"] = grace_until_iso
    if trial_until_iso:
        payload["trial_until"] = trial_until_iso

    try:
        db = supabase()
        # upsert prevents duplicates and works with unique(account_id)
        res = (
            db.table(table)
            .upsert(payload, on_conflict="account_id")
            .execute()
        )

        data = getattr(res, "data", None)
        return {"ok": True, "table": table, "row": (data[0] if isinstance(data, list) and data else data)}
    except Exception as e:
        return {"ok": False, "error": "db_insert_failed", "table": table, "message": repr(e)}


def get_subscription_status(account_id: str) -> Dict[str, Any]:
    """
    Backward-compatible export so older imports don't break boot.
    """
    return _get_sub_status(account_id)


def handle_payment_success(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Backward-compatible handler expected by app.routes.webhooks.
    Minimal safe implementation:
      - Extract account_id/user_id
      - Extract plan_code + expires_at if present
      - Upsert subscription
    """
    account_id = (payload.get("account_id") or payload.get("user_id") or payload.get("metadata", {}).get("account_id") or "").strip()
    plan_code = (payload.get("plan_code") or payload.get("plan") or payload.get("metadata", {}).get("plan_code") or "monthly").strip()
    expires_at = payload.get("expires_at") or payload.get("metadata", {}).get("expires_at")

    if not account_id:
        return {"ok": False, "error": "missing_account_id_in_payload"}

    # If expires_at is not provided, leave it NULL; status_service will treat it as none.
    res = activate_subscription_now(
        user_id=account_id,
        plan_code=plan_code,
        status="active",
        expires_at_iso=expires_at,
    )
    return {"ok": bool(res.get("ok")), "activated": res}
