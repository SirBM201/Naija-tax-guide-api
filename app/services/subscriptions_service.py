# app/services/subscriptions_service.py
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

from app.core.supabase_client import supabase  # <-- keep/adjust if your project path differs


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _request_id() -> str:
    return str(uuid.uuid4())


def _truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _debug_enabled() -> bool:
    # Optional: enable extra info in responses without leaking secrets
    return _truthy(os.getenv("DEBUG_SUBSCRIPTIONS"))


def _root_cause(where: str, e: Exception, *, req_id: str, hint: Optional[str] = None, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "where": where,
        "type": type(e).__name__,
        "message": str(e),
        "request_id": req_id,
    }
    if hint:
        out["hint"] = hint
    if extra:
        out["extra"] = extra
    return out


def _ok(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {"ok": True}
    if payload:
        out.update(payload)
    return out


def _err(error: str, message: str, *, root_cause: Optional[Dict[str, Any]] = None, req_id: Optional[str] = None, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {"ok": False, "error": error, "message": message}
    if req_id:
        out["request_id"] = req_id
    if root_cause:
        out["root_cause"] = root_cause
    if extra:
        out["extra"] = extra
    return out


# -----------------------------------------------------------------------------
# Subscription logic (minimal but stable)
# -----------------------------------------------------------------------------
def get_subscription_status(account_id: str) -> Dict[str, Any]:
    """
    Returns a normalized subscription status for an account_id.
    Safe: NEVER raises (always returns ok True/False).
    """
    req_id = _request_id()
    account_id = (account_id or "").strip()
    if not account_id:
        return _err("missing_account_id", "account_id is required", req_id=req_id)

    try:
        res = (
            supabase.table("user_subscriptions")
            .select("account_id, plan_code, status, starts_at, ends_at, updated_at, created_at")
            .eq("account_id", account_id)
            .limit(1)
            .execute()
        )
        rows = (res.data or []) if hasattr(res, "data") else []
        row = rows[0] if rows else None

        if not row:
            return _ok(
                {
                    "account_id": account_id,
                    "subscribed": False,
                    "plan_code": None,
                    "status": "none",
                    "starts_at": None,
                    "ends_at": None,
                }
            )

        # Determine active based on ends_at if present
        ends_at = row.get("ends_at")
        now = datetime.now(timezone.utc)

        active = False
        if ends_at:
            try:
                # Supabase often returns ISO string
                end_dt = datetime.fromisoformat(str(ends_at).replace("Z", "+00:00"))
                active = end_dt > now
            except Exception:
                # if ends_at unparsable, fall back to status field
                active = str(row.get("status") or "").lower() in {"active", "paid"}

        else:
            active = str(row.get("status") or "").lower() in {"active", "paid"}

        return _ok(
            {
                "account_id": account_id,
                "subscribed": bool(active),
                "plan_code": row.get("plan_code"),
                "status": "active" if active else (row.get("status") or "inactive"),
                "starts_at": row.get("starts_at"),
                "ends_at": row.get("ends_at"),
            }
        )

    except Exception as e:
        return _err(
            "get_subscription_status_failed",
            "could not read subscription status",
            req_id=req_id,
            root_cause=_root_cause(
                "subscriptions_service.get_subscription_status",
                e,
                req_id=req_id,
                hint="DB read failed (user_subscriptions). Check Supabase permissions, table name, and service role key.",
                extra={"account_id": account_id},
            ),
        )


def activate_subscription_now(*, account_id: str, plan_code: str = "monthly", days: Optional[int] = None) -> Dict[str, Any]:
    """
    Admin activation: creates/updates user_subscriptions for account_id.
    Safe: NEVER raises (always returns ok True/False).
    """
    req_id = _request_id()
    account_id = (account_id or "").strip()
    plan_code = (plan_code or "monthly").strip().lower()

    if not account_id:
        return _err("missing_account_id", "account_id is required", req_id=req_id)

    if plan_code not in {"monthly", "quarterly", "yearly"}:
        return _err("invalid_plan_code", "plan_code must be monthly|quarterly|yearly", req_id=req_id)

    # default duration
    if days is None:
        days = {"monthly": 30, "quarterly": 90, "yearly": 365}[plan_code]
    try:
        days = int(days)
        if days <= 0:
            return _err("invalid_days", "days must be > 0", req_id=req_id)
    except Exception:
        return _err("invalid_days", "days must be an integer", req_id=req_id)

    starts_at = datetime.now(timezone.utc)
    ends_at = starts_at + timedelta(days=days)

    try:
        # UPSERT by account_id (assumes account_id is unique in user_subscriptions)
        payload = {
            "account_id": account_id,
            "plan_code": plan_code,
            "status": "active",
            "starts_at": starts_at.isoformat(),
            "ends_at": ends_at.isoformat(),
            "updated_at": _now_iso(),
        }

        res = (
            supabase.table("user_subscriptions")
            .upsert(payload, on_conflict="account_id")
            .execute()
        )

        return _ok(
            {
                "account_id": account_id,
                "plan_code": plan_code,
                "status": "active",
                "starts_at": payload["starts_at"],
                "ends_at": payload["ends_at"],
                "request_id": req_id,
                "db": {"rows": len(res.data or [])} if _debug_enabled() else None,
            }
        )

    except Exception as e:
        return _err(
            "activate_subscription_failed",
            "could not activate subscription",
            req_id=req_id,
            root_cause=_root_cause(
                "subscriptions_service.activate_subscription_now",
                e,
                req_id=req_id,
                hint="DB upsert failed (user_subscriptions). Ensure table exists, account_id column type matches, and service role key is used on backend.",
                extra={"account_id": account_id, "plan_code": plan_code, "days": days},
            ),
        )


def debug_read_subscription(account_id: str) -> Dict[str, Any]:
    """
    Admin debug: returns raw user_subscriptions row + computed status.
    Safe: NEVER raises (always returns ok True/False).
    """
    req_id = _request_id()
    account_id = (account_id or "").strip()
    if not account_id:
        return _err("missing_account_id", "account_id is required", req_id=req_id)

    try:
        res = (
            supabase.table("user_subscriptions")
            .select("*")
            .eq("account_id", account_id)
            .limit(1)
            .execute()
        )
        rows = (res.data or []) if hasattr(res, "data") else []
        row = rows[0] if rows else None
        status = get_subscription_status(account_id)

        return _ok(
            {
                "account_id": account_id,
                "subscription_row": row,
                "computed_status": status,
                "request_id": req_id,
            }
        )

    except Exception as e:
        return _err(
            "debug_read_subscription_failed",
            "could not read subscription for debug",
            req_id=req_id,
            root_cause=_root_cause(
                "subscriptions_service.debug_read_subscription",
                e,
                req_id=req_id,
                hint="DB read failed. Check Supabase permissions and that user_subscriptions exists.",
                extra={"account_id": account_id},
            ),
        )


# -----------------------------------------------------------------------------
# Paystack webhook handler (kept here to avoid missing-import crashes)
# -----------------------------------------------------------------------------
def handle_payment_success(*, event_id: str, event_type: str, reference: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Called by app.routes.webhooks (or similar) on Paystack payment success.
    Stores event in paystack_events table (idempotent) and activates subscription (basic version).

    NOTE: Your paystack_events table has: id, event_id, event_type, reference, payload, created_at
    There is NO received_at (your SQL error confirmed that). Use created_at for ordering.
    """
    req_id = _request_id()
    event_id = (event_id or "").strip()
    event_type = (event_type or "").strip()
    reference = (reference or "").strip()

    if not event_id:
        return _err("missing_event_id", "event_id is required", req_id=req_id)
    if not event_type:
        return _err("missing_event_type", "event_type is required", req_id=req_id)
    if not reference:
        return _err("missing_reference", "reference is required", req_id=req_id)

    try:
        # 1) Insert Paystack event (idempotent if you add unique index on event_id)
        insert_payload = {
            "event_id": event_id,
            "event_type": event_type,
            "reference": reference,
            "payload": payload or {},
            "created_at": _now_iso(),
        }

        # If unique index on event_id exists, Supabase upsert prevents duplicates
        _ = supabase.table("paystack_events").upsert(insert_payload, on_conflict="event_id").execute()

        # 2) OPTIONAL: activate subscription based on payload metadata if present
        # Typical Paystack location: payload["data"]["metadata"] contains your account_id/plan.
        data = (payload or {}).get("data") or {}
        meta = data.get("metadata") or {}

        account_id = (meta.get("account_id") or "").strip()
        plan_code = (meta.get("plan_code") or "monthly").strip().lower()

        if account_id:
            act = activate_subscription_now(account_id=account_id, plan_code=plan_code, days=None)
            return _ok(
                {
                    "stored_event": True,
                    "activated": act.get("ok", False),
                    "activation_result": act,
                    "request_id": req_id,
                }
            )

        # If no account_id in metadata, we still succeed storing the event
        return _ok(
            {
                "stored_event": True,
                "activated": False,
                "warning": "event stored but no account_id found in payload.data.metadata.account_id",
                "request_id": req_id,
            }
        )

    except Exception as e:
        return _err(
            "handle_payment_success_failed",
            "failed processing paystack success event",
            req_id=req_id,
            root_cause=_root_cause(
                "subscriptions_service.handle_payment_success",
                e,
                req_id=req_id,
                hint="DB write failed (paystack_events) or activation failed. Confirm paystack_events table exists + service role key configured.",
                extra={"event_id": event_id, "event_type": event_type, "reference": reference},
            ),
        )
