# app/services/subscriptions_service.py
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from app.core.supabase_client import supabase

# -----------------------------
# Helpers
# -----------------------------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

def _as_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def _rootcause(where: str, e: Exception, *, req_id: str, hint: str = "", extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
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

def _truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "y", "on"}

def _require_service_key_hint() -> str:
    return "Ensure backend uses SUPABASE_SERVICE_ROLE_KEY (service role) for DB upserts/reads."

# -----------------------------
# Core: Subscription Status (used by ask_service)
# -----------------------------

def get_subscription_status(account_id: str) -> Dict[str, Any]:
    """
    Used by ask_service to decide free vs paid limits.
    Reads public.user_subscriptions by account_id.
    """
    req_id = str(uuid.uuid4())
    account_id = (account_id or "").strip()
    if not account_id:
        return {"ok": False, "error": "missing_account_id", "request_id": req_id}

    try:
        res = (
            supabase.table("user_subscriptions")
            .select("status, plan_code, current_period_end, started_at, updated_at")
            .eq("account_id", account_id)
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        row = rows[0] if rows else None

        if not row:
            return {
                "ok": True,
                "request_id": req_id,
                "is_paid": False,
                "plan_code": "free",
                "status": "none",
                "current_period_end": None,
            }

        status = (row.get("status") or "").lower()
        plan_code = (row.get("plan_code") or "free").lower()
        end_at = row.get("current_period_end")

        is_paid = status == "active"

        # expire if end passed
        try:
            if end_at:
                end_dt = datetime.fromisoformat(str(end_at).replace("Z", "+00:00"))
                if end_dt <= _utcnow():
                    is_paid = False
        except Exception:
            pass

        return {
            "ok": True,
            "request_id": req_id,
            "is_paid": bool(is_paid),
            "plan_code": plan_code,
            "status": status or "unknown",
            "current_period_end": end_at,
        }

    except Exception as e:
        return {
            "ok": False,
            "error": "get_subscription_status_failed",
            "request_id": req_id,
            "root_cause": _rootcause(
                "subscriptions_service.get_subscription_status",
                e,
                req_id=req_id,
                hint=f"DB read failed (user_subscriptions). {_require_service_key_hint()}",
                extra={"account_id": account_id},
            ),
        }

# -----------------------------
# Core: Activate Subscription (Admin endpoint)
# -----------------------------

def activate_subscription_now(account_id: str, plan_code: str, days: int = 30) -> Dict[str, Any]:
    """
    Called by /api/subscription/activate (admin protected).
    Upserts user_subscriptions row for this account_id.
    """
    req_id = str(uuid.uuid4())
    account_id = (account_id or "").strip()
    plan_code = (plan_code or "").strip().lower()

    if not account_id or not plan_code:
        return {"ok": False, "error": "missing_fields", "request_id": req_id, "need": ["account_id", "plan_code"]}

    try:
        days_i = int(days)
        if days_i <= 0:
            days_i = 30
    except Exception:
        days_i = 30

    now = _utcnow()
    end = now + timedelta(days=days_i)

    payload = {
        "account_id": account_id,
        "plan_code": plan_code,
        "status": "active",
        "started_at": _as_iso(now),
        "current_period_end": _as_iso(end),
        "updated_at": _as_iso(now),
    }

    try:
        # This assumes unique index exists on account_id (you created it).
        res = (
            supabase.table("user_subscriptions")
            .upsert(payload, on_conflict="account_id")
            .execute()
        )
        return {
            "ok": True,
            "request_id": req_id,
            "subscription": payload,
            "db": {"rows": getattr(res, "data", None)},
        }
    except Exception as e:
        return {
            "ok": False,
            "error": "activate_subscription_failed",
            "message": "could not activate subscription",
            "request_id": req_id,
            "root_cause": _rootcause(
                "subscriptions_service.activate_subscription_now",
                e,
                req_id=req_id,
                hint=f"DB upsert failed (user_subscriptions). Confirm table exists + account_id type matches. {_require_service_key_hint()}",
                extra={"account_id": account_id, "plan_code": plan_code, "days": days_i},
            ),
        }

# -----------------------------
# Paystack webhook handler
# -----------------------------

def handle_payment_success(evt: Dict[str, Any]) -> Dict[str, Any]:
    """
    Called by paystack webhook route.
    Expects: {event_id, provider, reference, account_id, plan_code, amount_kobo, currency, upgrade_mode, raw}
    Writes idempotent paystack_events then activates/updates subscription.
    """
    req_id = str(uuid.uuid4())
    try:
        event_id = (evt.get("event_id") or "").strip()
        provider = (evt.get("provider") or "paystack").strip().lower()
        reference = (evt.get("reference") or "").strip()
        account_id = (evt.get("account_id") or "").strip()
        plan_code = (evt.get("plan_code") or "").strip().lower()
        upgrade_mode = (evt.get("upgrade_mode") or "now").strip().lower()
        amount_kobo = evt.get("amount_kobo")
        currency = (evt.get("currency") or "NGN").strip().upper()
        raw = evt.get("raw") or {}

        if not event_id or not account_id or not plan_code:
            return {
                "ok": False,
                "error": "missing_fields",
                "request_id": req_id,
                "need": ["event_id", "account_id", "plan_code"],
            }

        # 1) Idempotency record (unique event_id index in paystack_events)
        try:
            supabase.table("paystack_events").insert(
                {
                    "event_id": event_id,
                    "provider": provider,
                    "reference": reference,
                    "account_id": account_id,
                    "plan_code": plan_code,
                    "amount_kobo": amount_kobo,
                    "currency": currency,
                    "raw": raw,
                    "created_at": _as_iso(_utcnow()),
                }
            ).execute()
        except Exception:
            # If unique violation or insert error, treat as already processed
            # (paystack retries same event_id)
            pass

        # 2) Apply subscription change
        if upgrade_mode not in ("now", "at_expiry"):
            upgrade_mode = "now"

        if upgrade_mode == "now":
            # activate immediately (default 30 days)
            out = activate_subscription_now(account_id, plan_code, days=30)
            out["request_id"] = req_id
            out["source"] = "handle_payment_success"
            return out

        # at_expiry: set pending upgrade fields if you have them
        # If you don't have pending columns, fallback to immediate activation.
        try:
            now = _utcnow()
            res = (
                supabase.table("user_subscriptions")
                .select("current_period_end, status, plan_code")
                .eq("account_id", account_id)
                .limit(1)
                .execute()
            )
            rows = getattr(res, "data", None) or []
            row = rows[0] if rows else None

            # If no existing subscription, activate now anyway
            if not row:
                out = activate_subscription_now(account_id, plan_code, days=30)
                out["request_id"] = req_id
                out["source"] = "handle_payment_success"
                out["note"] = "no existing subscription; activated immediately"
                return out

            # If already expired/inactive, activate now
            status = (row.get("status") or "").lower()
            end_at = row.get("current_period_end")
            expired = False
            try:
                if end_at:
                    end_dt = datetime.fromisoformat(str(end_at).replace("Z", "+00:00"))
                    expired = end_dt <= now
            except Exception:
                expired = False

            if status != "active" or expired:
                out = activate_subscription_now(account_id, plan_code, days=30)
                out["request_id"] = req_id
                out["source"] = "handle_payment_success"
                out["note"] = "existing subscription inactive/expired; activated immediately"
                return out

            # Try to store pending plan (optional columns)
            pending_payload = {
                "account_id": account_id,
                "pending_plan_code": plan_code,
                "pending_upgrade_mode": "at_expiry",
                "updated_at": _as_iso(now),
            }
            supabase.table("user_subscriptions").upsert(
                pending_payload,
                on_conflict="account_id",
            ).execute()

            return {
                "ok": True,
                "request_id": req_id,
                "mode": "at_expiry",
                "message": "upgrade scheduled at expiry",
                "account_id": account_id,
                "plan_code": plan_code,
            }
        except Exception:
            out = activate_subscription_now(account_id, plan_code, days=30)
            out["request_id"] = req_id
            out["source"] = "handle_payment_success"
            out["note"] = "at_expiry scheduling failed; activated immediately"
            return out

    except Exception as e:
        return {
            "ok": False,
            "error": "handle_payment_success_failed",
            "request_id": req_id,
            "root_cause": _rootcause("subscriptions_service.handle_payment_success", e, req_id=req_id),
        }

# -----------------------------
# Debug helper (optional endpoint usage)
# -----------------------------

def debug_read_subscription(account_id: str) -> Dict[str, Any]:
    req_id = str(uuid.uuid4())
    account_id = (account_id or "").strip()
    if not account_id:
        return {"ok": False, "error": "missing_account_id", "request_id": req_id}

    try:
        res = (
            supabase.table("user_subscriptions")
            .select("*")
            .eq("account_id", account_id)
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        return {"ok": True, "request_id": req_id, "row": (rows[0] if rows else None)}
    except Exception as e:
        return {
            "ok": False,
            "error": "debug_read_subscription_failed",
            "request_id": req_id,
            "root_cause": _rootcause(
                "subscriptions_service.debug_read_subscription",
                e,
                req_id=req_id,
                hint=f"DB read failed (user_subscriptions). {_require_service_key_hint()}",
                extra={"account_id": account_id},
            ),
        }
