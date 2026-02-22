# app/services/subscriptions_service.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.core.supabase_client import supabase


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(v: Any) -> Optional[datetime]:
    if not v:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        # accept "Z"
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(s)
        except Exception:
            return None
    return None


def _is_active_row(row: Dict[str, Any]) -> bool:
    status = (row.get("status") or "").strip().lower()
    if status not in {"active", "trial"}:
        return False
    expires_at = _parse_dt(row.get("expires_at"))
    if expires_at and expires_at <= _now_utc():
        return False
    return True


def get_subscription_status(user_id: Optional[str]) -> Dict[str, Any]:
    """
    Global-standard shape for gating:
      - active: bool
      - state: "active"|"trial"|"none"|"expired"|"canceled"|...
      - reason: machine friendly
      - plan_code, expires_at, grace_until
    """
    if not user_id:
        return {
            "ok": True,
            "active": False,
            "state": "none",
            "reason": "missing_user_id",
            "plan_code": None,
            "expires_at": None,
            "grace_until": None,
        }

    try:
        res = (
            supabase.table("subscriptions")
            .select("id, user_id, plan_code, status, expires_at, grace_until, created_at, updated_at")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = (res.data or []) if hasattr(res, "data") else []
        if not rows:
            return {
                "ok": True,
                "active": False,
                "state": "none",
                "reason": "no_subscription",
                "plan_code": None,
                "expires_at": None,
                "grace_until": None,
                "account_id": user_id,
            }

        row = rows[0]
        status = (row.get("status") or "").strip().lower()
        plan_code = (row.get("plan_code") or "").strip() or None
        expires_at = row.get("expires_at")
        grace_until = row.get("grace_until")

        if _is_active_row(row):
            return {
                "ok": True,
                "active": True,
                "state": "trial" if status == "trial" else "active",
                "reason": "active_subscription",
                "plan_code": plan_code,
                "expires_at": expires_at,
                "grace_until": grace_until,
                "account_id": user_id,
            }

        # not active -> classify why
        exp = _parse_dt(expires_at)
        if exp and exp <= _now_utc():
            reason = "expired"
            state = "expired"
        elif status in {"canceled", "cancelled"}:
            reason = "canceled"
            state = "canceled"
        else:
            reason = "inactive"
            state = status or "inactive"

        return {
            "ok": True,
            "active": False,
            "state": state,
            "reason": reason,
            "plan_code": plan_code,
            "expires_at": expires_at,
            "grace_until": grace_until,
            "account_id": user_id,
        }
    except Exception as e:
        return {
            "ok": False,
            "active": False,
            "state": "error",
            "reason": "exception",
            "message": str(e),
            "plan_code": None,
            "expires_at": None,
            "grace_until": None,
            "account_id": user_id,
        }


def activate_subscription_now(
    user_id: str,
    plan_code: str = "manual",
    expires_at_iso: Optional[str] = None,
    status: str = "active",
) -> Dict[str, Any]:
    """
    Admin/test helper: upsert a subscription row.
    """
    if not user_id:
        return {"ok": False, "error": "missing_user_id"}

    plan_code = (plan_code or "manual").strip()
    status = (status or "active").strip()

    expires_at: Optional[str] = None
    if expires_at_iso:
        # store raw iso string (DB column is typically timestamptz; supabase will parse)
        expires_at = str(expires_at_iso).strip() or None

    payload = {
        "user_id": user_id,
        "plan_code": plan_code,
        "status": status,
        "expires_at": expires_at,
        "updated_at": _now_utc().isoformat(),
    }

    try:
        res = supabase.table("subscriptions").insert(payload).execute()
        return {
            "ok": True,
            "action": "inserted",
            "user_id": user_id,
            "plan_code": plan_code,
            "status": status,
            "expires_at": expires_at,
            "db": getattr(res, "data", None),
        }
    except Exception:
        # fallback to update most recent row if insert fails due to constraints in your schema
        try:
            # fetch latest id
            get = (
                supabase.table("subscriptions")
                .select("id")
                .eq("user_id", user_id)
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            rows = (get.data or []) if hasattr(get, "data") else []
            if not rows:
                raise RuntimeError("no existing subscription row to update")
            sub_id = rows[0]["id"]

            upd = supabase.table("subscriptions").update(payload).eq("id", sub_id).execute()
            return {
                "ok": True,
                "action": "updated",
                "id": sub_id,
                "user_id": user_id,
                "plan_code": plan_code,
                "status": status,
                "expires_at": expires_at,
                "db": getattr(upd, "data", None),
            }
        except Exception as e2:
            return {"ok": False, "error": "db_write_failed", "message": str(e2)}


def handle_payment_success(user_id: str, plan_code: str, expires_at_iso: Optional[str] = None) -> Dict[str, Any]:
    """
    Used by webhooks routes after a successful payment event.
    Keep this function name stable because routes import it.
    """
    return activate_subscription_now(
        user_id=user_id,
        plan_code=plan_code,
        expires_at_iso=expires_at_iso,
        status="active",
    )
