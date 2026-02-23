# app/services/subscriptions_service.py
from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional, Tuple

from app.core.supabase_client import supabase

# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

def _to_dt(v: Any) -> Optional[datetime]:
    if not v:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, str):
        try:
            # Handles "2026-03-24T15:12:54.903861+00:00" or "Z"
            s = v.replace("Z", "+00:00")
            dt = datetime.fromisoformat(s)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None
    return None

def _to_iso(dt: Optional[datetime]) -> Optional[str]:
    if not dt:
        return None
    if not dt.tzinfo:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()

def _sb():
    # In this codebase, supabase is a factory function (callable)
    return supabase() if callable(supabase) else supabase

def _truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "y", "on"}

# -------------------------------------------------------------------
# Config
# -------------------------------------------------------------------

SUBSCRIPTIONS_TABLE = (os.getenv("SUBSCRIPTIONS_TABLE", "") or "").strip() or "subscriptions"
PAYSTACK_TX_TABLE = (os.getenv("PAYSTACK_TX_TABLE", "") or "").strip() or "paystack_transactions"

TRIAL_DAYS = int((os.getenv("TRIAL_DAYS", "7") or "7").strip())
GRACE_DAYS = int((os.getenv("SUBSCRIPTION_GRACE_DAYS", "3") or "3").strip())

# You can tune these later; these are “global default” durations.
PLAN_DAYS = {
    "monthly": 30,
    "quarterly": 90,
    "yearly": 365,
    "annual": 365,
    "trial": TRIAL_DAYS,
    "manual": 30,
}

# -------------------------------------------------------------------
# Status computation (global-standard)
# -------------------------------------------------------------------

def _compute_state(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Output is stable for frontend gating:

    state:
      - none
      - trialing
      - active
      - grace
      - expired
      - canceled
      - error
    """
    base: Dict[str, Any] = {
        "ok": True,
        "active": False,
        "state": "none",
        "reason": "no_subscription",
        "plan_code": None,
        "expires_at": None,
        "grace_until": None,
        "message": None,
    }

    if not row:
        return base

    try:
        now = _utcnow()
        status = str(row.get("status") or "").strip().lower()
        plan_code = str(row.get("plan_code") or row.get("plan") or "").strip().lower() or None

        expires_at = _to_dt(row.get("expires_at"))
        grace_until = _to_dt(row.get("grace_until"))

        base["plan_code"] = plan_code
        base["expires_at"] = _to_iso(expires_at)
        base["grace_until"] = _to_iso(grace_until)

        if status in {"canceled", "cancelled"}:
            base["state"] = "canceled"
            base["reason"] = "canceled"
            base["active"] = False
            return base

        # Trial should count as active for access control if within window
        if status in {"trial", "trialing"}:
            # If no expires_at, treat as valid for TRIAL_DAYS from created_at (best-effort)
            if not expires_at:
                created_at = _to_dt(row.get("created_at"))
                if created_at:
                    expires_at = created_at + timedelta(days=TRIAL_DAYS)
                    base["expires_at"] = _to_iso(expires_at)

            if expires_at and expires_at > now:
                base["state"] = "trialing"
                base["reason"] = "trial_active"
                base["active"] = True
                return base

            # Trial ended -> expired (optionally grace)
            if expires_at and expires_at <= now:
                base["state"] = "expired"
                base["reason"] = "trial_ended"
                base["active"] = False
                return base

        # Active subscription logic
        if status in {"active", "paid"}:
            # If expires_at missing, treat as active (manual lifetime)
            if not expires_at:
                base["state"] = "active"
                base["reason"] = "active_no_expiry"
                base["active"] = True
                return base

            if expires_at > now:
                base["state"] = "active"
                base["reason"] = "active_in_window"
                base["active"] = True
                return base

            # expired: apply grace
            if grace_until and grace_until > now:
                base["state"] = "grace"
                base["reason"] = "in_grace"
                base["active"] = True  # global standard: you may keep access during grace
                return base

            base["state"] = "expired"
            base["reason"] = "expired"
            base["active"] = False
            return base

        # If status unknown but we have dates, compute from them as fallback
        if expires_at:
            if expires_at > now:
                base["state"] = "active"
                base["reason"] = "active_by_expiry"
                base["active"] = True
                return base

            if grace_until and grace_until > now:
                base["state"] = "grace"
                base["reason"] = "grace_by_expiry"
                base["active"] = True
                return base

            base["state"] = "expired"
            base["reason"] = "expired_by_expiry"
            base["active"] = False
            return base

        base["state"] = "none"
        base["reason"] = "unknown_status"
        base["active"] = False
        return base

    except Exception as e:
        return {
            **base,
            "ok": False,
            "active": False,
            "state": "error",
            "reason": "exception",
            "message": repr(e),
        }

# -------------------------------------------------------------------
# DB operations
# -------------------------------------------------------------------

def _get_latest_row(account_id: str) -> Optional[Dict[str, Any]]:
    if not account_id:
        return None
    res = (
        _sb()
        .table(SUBSCRIPTIONS_TABLE)
        .select("*")
        .eq("account_id", account_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    rows = (res.data or []) if hasattr(res, "data") else []
    return rows[0] if rows else None

def get_subscription_status(account_id: Optional[str]) -> Dict[str, Any]:
    """
    Public: called by /subscription/status and by auth_context_service.
    """
    if not account_id:
        return {
            "ok": True,
            "account_id": None,
            "active": False,
            "state": "none",
            "reason": "missing_account_id",
            "plan_code": None,
            "expires_at": None,
            "grace_until": None,
        }

    row = None
    try:
        row = _get_latest_row(account_id)
    except Exception as e:
        st = _compute_state(None)
        st.update(
            {
                "ok": False,
                "account_id": account_id,
                "state": "error",
                "reason": "db_error",
                "message": repr(e),
            }
        )
        return st

    st = _compute_state(row)
    st["account_id"] = account_id
    return st

def activate_subscription_now(
    account_id: Optional[str] = None,
    user_id: Optional[str] = None,
    plan_code: str = "manual",
    expires_at_iso: Optional[str] = None,
    status: str = "active",
    metadata: Optional[Dict[str, Any]] = None,
    extend_if_active: bool = True,
) -> Dict[str, Any]:
    """
    ADMIN/WEBHOOK: creates or updates a subscription record.

    Supports both `account_id` and `user_id` because your routes use both names.
    """
    aid = (account_id or user_id or "").strip()
    if not aid:
        return {"ok": False, "error": "missing_account_id"}

    plan = (plan_code or "manual").strip().lower()
    status_norm = (status or "active").strip().lower()

    now = _utcnow()
    current = None
    try:
        current = _get_latest_row(aid)
    except Exception:
        current = None

    # Determine expiry
    expires_at = _to_dt(expires_at_iso)

    if not expires_at:
        days = PLAN_DAYS.get(plan, PLAN_DAYS.get("manual", 30))
        base_start = now

        # Global-standard: extend from current expiry if still active and extension is enabled
        if extend_if_active and current:
            cur_state = _compute_state(current)
            cur_exp = _to_dt(current.get("expires_at"))
            if cur_state.get("active") and cur_exp and cur_exp > now:
                base_start = cur_exp

        expires_at = base_start + timedelta(days=int(days))

    grace_until = expires_at + timedelta(days=GRACE_DAYS) if GRACE_DAYS > 0 else None

    row = {
        "account_id": aid,
        "plan_code": plan,
        "status": status_norm,
        "started_at": _to_iso(now),
        "expires_at": _to_iso(expires_at),
        "grace_until": _to_iso(grace_until),
        "metadata": metadata or {},
        "updated_at": _to_iso(now),
        "source": "admin_or_webhook",
    }

    # Insert a new row (append-only is safer for auditability)
    try:
        ins = _sb().table(SUBSCRIPTIONS_TABLE).insert(row).execute()
        inserted = (ins.data or []) if hasattr(ins, "data") else []
        latest = inserted[0] if inserted else row
    except Exception as e:
        return {"ok": False, "error": "db_insert_failed", "message": repr(e)}

    out = _compute_state(latest)
    out["account_id"] = aid
    out["ok"] = True
    out["latest_row"] = latest
    return out

# -------------------------------------------------------------------
# Compatibility exports required by routes/webhooks.py
# -------------------------------------------------------------------

def handle_payment_success(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compatibility hook for app.routes.webhooks.

    Expected payload keys (best-effort):
      - account_id
      - plan_code
      - reference (optional)
      - provider (optional)
    """
    account_id = str(payload.get("account_id") or payload.get("user_id") or "").strip()
    plan_code = str(payload.get("plan_code") or payload.get("plan") or "").strip().lower() or "monthly"
    reference = str(payload.get("reference") or "").strip()

    if not account_id:
        return {"ok": False, "error": "missing_account_id"}

    meta = {
        "provider": payload.get("provider") or "unknown",
        "reference": reference,
        "raw": payload,
    }

    # Treat trial as its own status, but still “active” in compute_state if within expiry.
    status = "trial" if plan_code == "trial" else "active"

    res = activate_subscription_now(
        account_id=account_id,
        plan_code=plan_code,
        status=status,
        metadata=meta,
        extend_if_active=True,
    )

    # Optionally stamp transaction table if reference exists
    if reference:
        try:
            _sb().table(PAYSTACK_TX_TABLE).update(
                {"status": "success", "account_id": account_id, "plan_code": plan_code}
            ).eq("reference", reference).execute()
        except Exception:
            pass

    return res

def handle_payment_failure(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compatibility hook for app.routes.webhooks.
    """
    reference = str(payload.get("reference") or "").strip()
    if reference:
        try:
            _sb().table(PAYSTACK_TX_TABLE).update({"status": "failed", "raw": payload}).eq("reference", reference).execute()
        except Exception:
            pass
    return {"ok": True}

def handle_subscription_canceled(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Optional hook; safe if called.
    """
    account_id = str(payload.get("account_id") or payload.get("user_id") or "").strip()
    if not account_id:
        return {"ok": False, "error": "missing_account_id"}

    now = _utcnow()
    row = {
        "account_id": account_id,
        "plan_code": str(payload.get("plan_code") or payload.get("plan") or "").strip().lower() or None,
        "status": "canceled",
        "started_at": _to_iso(now),
        "expires_at": None,
        "grace_until": None,
        "metadata": {"raw": payload, "event": "canceled"},
        "updated_at": _to_iso(now),
        "source": "webhook",
    }
    try:
        _sb().table(SUBSCRIPTIONS_TABLE).insert(row).execute()
    except Exception as e:
        return {"ok": False, "error": "db_insert_failed", "message": repr(e)}
    return {"ok": True}
