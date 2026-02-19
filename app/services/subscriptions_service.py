# app/services/subscriptions_service.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from app.core.supabase_client import supabase


# -----------------------------
# Helpers
# -----------------------------
def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: str | None) -> Optional[datetime]:
    if not value:
        return None
    try:
        v = value.replace("Z", "+00:00")
        return datetime.fromisoformat(v)
    except Exception:
        return None


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _sb():
    # support both "supabase instance" and "factory"
    return supabase() if callable(supabase) else supabase


def _find_account_id(
    account_id: Optional[str],
    provider: Optional[str],
    provider_user_id: Optional[str],
) -> Optional[str]:
    """
    If account_id is given -> use it.
    Else try to find account by (provider, provider_user_id) from accounts table.
    """
    if account_id:
        v = account_id.strip()
        return v or None

    if not provider or not provider_user_id:
        return None

    sb = _sb()
    res = (
        sb.table("accounts")
        .select("account_id")
        .eq("provider", provider)
        .eq("provider_user_id", provider_user_id)
        .limit(1)
        .execute()
    )
    rows = getattr(res, "data", None) or []
    if not rows:
        return None
    return (rows[0].get("account_id") or "").strip() or None


def _safe_select_one(table: str, select: str, **eq_filters) -> Optional[Dict[str, Any]]:
    """
    Best-effort select 1 row. If table doesn't exist or any error -> None.
    """
    try:
        sb = _sb()
        q = sb.table(table).select(select)
        for k, v in eq_filters.items():
            q = q.eq(k, v)
        res = q.limit(1).execute()
        rows = getattr(res, "data", None) or []
        return rows[0] if rows else None
    except Exception:
        return None


def _safe_upsert(table: str, payload: Dict[str, Any], on_conflict: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Best-effort upsert. If table doesn't exist or any error -> None.
    """
    try:
        sb = _sb()
        if on_conflict:
            res = sb.table(table).upsert(payload, on_conflict=on_conflict).execute()
        else:
            res = sb.table(table).upsert(payload).execute()
        rows = getattr(res, "data", None) or []
        return rows[0] if rows else payload
    except Exception:
        return None


# -----------------------------
# Subscription Status
# -----------------------------
def get_subscription_status(
    account_id: Optional[str] = None,
    provider: Optional[str] = None,
    provider_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Returns a stable frontend-friendly shape.
    """
    resolved = _find_account_id(account_id, provider, provider_user_id)

    out = {
        "account_id": resolved,
        "active": False,
        "expires_at": None,
        "grace_until": None,
        "plan_code": None,
        "reason": "none",
        "state": "none",  # none|active|grace|expired
    }

    if not resolved:
        out["reason"] = "no_account"
        return out

    sb = _sb()
    try:
        res = (
            sb.table("subscriptions")
            .select("account_id, plan_code, status, expires_at, grace_until, next_plan_code, updated_at")
            .eq("account_id", resolved)
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        if not rows:
            out["reason"] = "no_subscription"
            return out

        row = rows[0] or {}
        out["plan_code"] = row.get("plan_code")
        out["expires_at"] = row.get("expires_at")
        out["grace_until"] = row.get("grace_until")

        now = _now_utc()
        exp = _parse_iso(row.get("expires_at"))
        grace = _parse_iso(row.get("grace_until"))

        if exp and now <= exp:
            out["active"] = True
            out["state"] = "active"
            out["reason"] = "active"
            return out

        if grace and now <= grace:
            out["active"] = True
            out["state"] = "grace"
            out["reason"] = "grace"
            return out

        out["active"] = False
        out["state"] = "expired"
        out["reason"] = "expired"
        return out

    except Exception:
        out["reason"] = "status_lookup_failed"
        return out


# -----------------------------
# Activation / Change
# -----------------------------
def activate_subscription_now(
    account_id: str,
    plan_code: str,
    status: str = "active",
    duration_days: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Activates immediately. If duration_days is None, use defaults:
      monthly=30, quarterly=90, yearly=365, trial=7
    """
    plan_code = (plan_code or "").strip().lower() or "manual"
    now = _now_utc()

    default_days = {
        "monthly": 30,
        "quarterly": 90,
        "yearly": 365,
        "trial": 7,
        "manual": 30,
    }
    days = duration_days if isinstance(duration_days, int) and duration_days > 0 else default_days.get(plan_code, 30)
    expires_at = now + timedelta(days=days)

    sb = _sb()
    payload = {
        "account_id": account_id,
        "plan_code": plan_code,
        "status": status,
        "expires_at": _iso(expires_at),
        "grace_until": None,
        "next_plan_code": None,
        "updated_at": _iso(now),
    }

    res = sb.table("subscriptions").upsert(payload, on_conflict="account_id").execute()
    rows = getattr(res, "data", None) or []
    return rows[0] if rows else payload


def manual_activate_subscription(
    account_id: str,
    plan_code: str = "manual",
    expires_at: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Admin tool: set any plan_code + optional exact expiry timestamp.
    """
    now = _now_utc()
    exp = _parse_iso(expires_at) if expires_at else None
    if exp is None:
        exp = now + timedelta(days=30)

    sb = _sb()
    payload = {
        "account_id": account_id,
        "plan_code": (plan_code or "manual").strip().lower(),
        "status": "active",
        "expires_at": _iso(exp),
        "grace_until": None,
        "next_plan_code": None,
        "updated_at": _iso(now),
    }
    res = sb.table("subscriptions").upsert(payload, on_conflict="account_id").execute()
    rows = getattr(res, "data", None) or []
    return rows[0] if rows else payload


def start_trial_if_eligible(account_id: str, trial_plan_code: str = "trial") -> Dict[str, Any]:
    """
    Starts a trial only if user has no subscription row yet.
    """
    sb = _sb()
    try:
        res = (
            sb.table("subscriptions")
            .select("account_id")
            .eq("account_id", account_id)
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        if rows:
            return {"ok": False, "error": "trial_not_eligible", "reason": "already_has_subscription"}

        sub = activate_subscription_now(account_id=account_id, plan_code=trial_plan_code, status="active", duration_days=7)
        return {"ok": True, "subscription": sub}

    except Exception:
        return {"ok": False, "error": "trial_failed"}


def schedule_plan_change_at_expiry(account_id: str, next_plan_code: str) -> Dict[str, Any]:
    """
    Stores next_plan_code on the subscription row.
    """
    sb = _sb()
    now = _now_utc()

    res = (
        sb.table("subscriptions")
        .select("account_id, plan_code, status, expires_at, grace_until, next_plan_code")
        .eq("account_id", account_id)
        .limit(1)
        .execute()
    )
    rows = getattr(res, "data", None) or []
    if not rows:
        payload = {
            "account_id": account_id,
            "plan_code": None,
            "status": "none",
            "expires_at": None,
            "grace_until": None,
            "next_plan_code": (next_plan_code or "").strip().lower(),
            "updated_at": _iso(now),
        }
        up = sb.table("subscriptions").upsert(payload, on_conflict="account_id").execute()
        data = getattr(up, "data", None) or []
        return data[0] if data else payload

    upd = (
        sb.table("subscriptions")
        .update({"next_plan_code": (next_plan_code or "").strip().lower(), "updated_at": _iso(now)})
        .eq("account_id", account_id)
        .execute()
    )
    data = getattr(upd, "data", None) or []
    return data[0] if data else rows[0]


# -----------------------------
# Webhook Payment Success Handler (FIXES YOUR CRASH)
# -----------------------------
def handle_payment_success(
    *,
    reference: Optional[str] = None,
    account_id: Optional[str] = None,
    provider: Optional[str] = None,
    provider_user_id: Optional[str] = None,
    plan_code: Optional[str] = None,
    amount: Optional[int] = None,
    currency: Optional[str] = None,
    paid_at: Optional[str] = None,
    raw: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    ✅ This function is imported by app/routes/webhooks.py.
    It must exist or the app will crash at boot.

    What it does:
    - resolves account_id
    - idempotency check (best-effort) using 'payments' table if it exists
    - activates subscription immediately based on plan_code
    - records payment (best-effort) if payments table exists
    - returns a clean dict for webhooks.py to jsonify

    NOTE:
    - This is safe even if you don't yet have payments table; it won't crash.
    """
    resolved = _find_account_id(account_id, provider, provider_user_id)
    if not resolved:
        return {"ok": False, "error": "no_account", "reference": reference}

    pcode = (plan_code or "").strip().lower() or "monthly"
    now = _now_utc()

    # ---- Idempotency (best-effort) ----
    # If you have a payments table with a unique "reference", this prevents double activation.
    if reference:
        existing = _safe_select_one("payments", "reference, status, account_id, plan_code, created_at", reference=reference)
        if existing and (existing.get("status") in ("success", "succeeded", "paid")):
            # already handled before
            status = get_subscription_status(account_id=resolved)
            return {
                "ok": True,
                "idempotent": True,
                "reference": reference,
                "account_id": resolved,
                "plan_code": status.get("plan_code"),
                "expires_at": status.get("expires_at"),
                "subscription_status": status,
            }

    # ---- Activate subscription ----
    sub = activate_subscription_now(account_id=resolved, plan_code=pcode, status="active", duration_days=None)

    # ---- Record payment (best-effort) ----
    if reference:
        pay_payload = {
            "reference": reference,
            "account_id": resolved,
            "plan_code": pcode,
            "amount": amount,
            "currency": currency or "NGN",
            "status": "success",
            "paid_at": paid_at,
            "created_at": _iso(now),
            "raw": raw,
        }
        # If you have unique on reference: on_conflict="reference"
        _safe_upsert("payments", pay_payload, on_conflict="reference")

    return {
        "ok": True,
        "reference": reference,
        "account_id": resolved,
        "plan_code": sub.get("plan_code"),
        "expires_at": sub.get("expires_at"),
        "subscription": sub,
    }
