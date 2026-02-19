# app/services/subscriptions_service.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, Tuple

from ..core.supabase_client import supabase as _supabase


# -----------------------------
# Supabase client compat
# (supports supabase() factory OR supabase client instance)
# -----------------------------
def sb():
    return _supabase() if callable(_supabase) else _supabase


# -----------------------------
# Time helpers
# -----------------------------
def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: str) -> Optional[datetime]:
    try:
        v = value.replace("Z", "+00:00")
        return datetime.fromisoformat(v)
    except Exception:
        return None


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# -----------------------------
# Lookups
# -----------------------------
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
        return (account_id or "").strip() or None

    if not provider or not provider_user_id:
        return None

    try:
        res = (
            sb()
            .table("accounts")
            .select("account_id")
            .eq("provider", provider)
            .eq("provider_user_id", provider_user_id)
            .limit(1)
            .execute()
        )
        rows = (res.data or []) if hasattr(res, "data") else []
        return rows[0]["account_id"] if rows else None
    except Exception:
        return None


def _get_active_subscription_row(account_id: str) -> Optional[Dict[str, Any]]:
    try:
        res = (
            sb()
            .table("user_subscriptions")
            .select("*")
            .eq("account_id", account_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = (res.data or []) if hasattr(res, "data") else []
        return rows[0] if rows else None
    except Exception:
        return None


# -----------------------------
# Public: subscription status
# -----------------------------
def get_subscription_status(
    account_id: Optional[str] = None,
    provider: Optional[str] = None,
    provider_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Returns a normalized status used by frontend + enforcement.
    """
    aid = _find_account_id(account_id, provider, provider_user_id)
    if not aid:
        return {
            "account_id": None,
            "active": False,
            "expires_at": None,
            "grace_until": None,
            "plan_code": None,
            "reason": "missing_account",
            "state": "none",
        }

    row = _get_active_subscription_row(aid)
    if not row:
        return {
            "account_id": aid,
            "active": False,
            "expires_at": None,
            "grace_until": None,
            "plan_code": None,
            "reason": "no_subscription",
            "state": "none",
        }

    now = _now_utc()
    expires_at = _parse_iso(row.get("expires_at") or "") if row.get("expires_at") else None
    grace_until = _parse_iso(row.get("grace_until") or "") if row.get("grace_until") else None

    plan_code = row.get("plan_code")
    if not expires_at:
        return {
            "account_id": aid,
            "active": False,
            "expires_at": None,
            "grace_until": None,
            "plan_code": plan_code,
            "reason": "bad_expires_at",
            "state": "expired",
        }

    if expires_at > now:
        return {
            "account_id": aid,
            "active": True,
            "expires_at": _iso(expires_at),
            "grace_until": _iso(grace_until) if grace_until else None,
            "plan_code": plan_code,
            "reason": "active",
            "state": "active",
        }

    # expired; maybe still in grace
    if grace_until and grace_until > now:
        return {
            "account_id": aid,
            "active": True,
            "expires_at": _iso(expires_at),
            "grace_until": _iso(grace_until),
            "plan_code": plan_code,
            "reason": "grace",
            "state": "grace",
        }

    return {
        "account_id": aid,
        "active": False,
        "expires_at": _iso(expires_at),
        "grace_until": _iso(grace_until) if grace_until else None,
        "plan_code": plan_code,
        "reason": "expired",
        "state": "expired",
    }


# -----------------------------
# Trial
# -----------------------------
def start_trial_if_eligible(account_id: str) -> Tuple[bool, str]:
    """
    Lightweight stub: start a trial only if user has no subscription yet.
    If you already have a more complex trial policy, keep it here.
    """
    account_id = (account_id or "").strip()
    if not account_id:
        return False, "missing_account_id"

    existing = _get_active_subscription_row(account_id)
    if existing:
        return False, "already_has_subscription"

    # If you have a trial plan in "plans" table, use it; else skip.
    trial_plan_code = "trial"
    try:
        plan = (
            sb()
            .table("plans")
            .select("plan_code, duration_days")
            .eq("plan_code", trial_plan_code)
            .eq("active", True)
            .limit(1)
            .execute()
        )
        prow = (plan.data or []) if hasattr(plan, "data") else []
        if not prow:
            return False, "trial_plan_not_configured"

        duration_days = int(prow[0].get("duration_days") or 7)
        now = _now_utc()
        expires_at = now + timedelta(days=duration_days)
        grace_until = expires_at + timedelta(days=2)

        sb().table("user_subscriptions").insert(
            {
                "account_id": account_id,
                "plan_code": trial_plan_code,
                "expires_at": _iso(expires_at),
                "grace_until": _iso(grace_until),
                "status": "active",
                "source": "trial",
            }
        ).execute()

        return True, "trial_started"
    except Exception:
        return False, "trial_start_failed"


# -----------------------------
# Activation / upgrades
# -----------------------------
def activate_subscription_now(
    account_id: str,
    plan_code: str,
    reference: Optional[str] = None,
    paid_at: Optional[str] = None,
    source: str = "paystack",
) -> Tuple[bool, str]:
    """
    Activate plan immediately from now (reset expiry from now).
    """
    account_id = (account_id or "").strip()
    plan_code = (plan_code or "").strip()

    if not account_id or not plan_code:
        return False, "missing_account_or_plan"

    try:
        # fetch plan duration
        res = (
            sb()
            .table("plans")
            .select("plan_code, duration_days, active")
            .eq("plan_code", plan_code)
            .eq("active", True)
            .limit(1)
            .execute()
        )
        rows = (res.data or []) if hasattr(res, "data") else []
        if not rows:
            return False, "plan_not_found_or_inactive"

        duration_days = int(rows[0].get("duration_days") or 30)
        now = _now_utc()
        expires_at = now + timedelta(days=duration_days)
        grace_until = expires_at + timedelta(days=2)

        # create a new subscription row (append history)
        sb().table("user_subscriptions").insert(
            {
                "account_id": account_id,
                "plan_code": plan_code,
                "expires_at": _iso(expires_at),
                "grace_until": _iso(grace_until),
                "status": "active",
                "source": source,
                "reference": reference,
                "paid_at": paid_at,
            }
        ).execute()

        return True, "activated"
    except Exception:
        return False, "activation_failed"


def schedule_plan_change_at_expiry(
    account_id: str,
    next_plan_code: str,
    effective_after: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Schedules a plan change when current plan expires.
    Implementation: store "pending_plan_code" on latest subscription row (if column exists),
    else store into a lightweight table if you have it.
    """
    account_id = (account_id or "").strip()
    next_plan_code = (next_plan_code or "").strip()
    if not account_id or not next_plan_code:
        return False, "missing_account_or_plan"

    row = _get_active_subscription_row(account_id)
    if not row:
        return False, "no_existing_subscription"

    sub_id = row.get("id")
    if not sub_id:
        return False, "bad_subscription_row"

    try:
        payload = {"pending_plan_code": next_plan_code}
        if effective_after:
            payload["pending_effective_after"] = effective_after

        # If the column doesn't exist, this will error; you can later add it.
        sb().table("user_subscriptions").update(payload).eq("id", sub_id).execute()
        return True, "scheduled"
    except Exception:
        return False, "schedule_failed_add_columns_pending_plan_code"


def manual_activate_subscription(account_id: str, plan_code: str) -> Tuple[bool, str]:
    """
    Admin/manual activation helper expected by routes/subscriptions.py
    """
    return activate_subscription_now(
        account_id=account_id,
        plan_code=plan_code,
        reference="manual",
        paid_at=_iso(_now_utc()),
        source="manual",
    )


# -----------------------------
# Paystack webhook bridge
# -----------------------------
def handle_payment_success(payload: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Expected by routes/webhooks.py
    Payload may include:
      - account_id
      - plan_code
      - reference
      - paid_at
    """
    payload = payload or {}
    account_id = (payload.get("account_id") or "").strip()
    plan_code = (payload.get("plan_code") or "").strip()
    reference = (payload.get("reference") or "").strip() or None
    paid_at = (payload.get("paid_at") or "").strip() or None

    if not account_id or not plan_code:
        return False, "missing_account_or_plan"

    return activate_subscription_now(
        account_id=account_id,
        plan_code=plan_code,
        reference=reference,
        paid_at=paid_at,
        source="paystack",
    )
