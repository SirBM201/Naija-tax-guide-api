# app/services/subscriptions_service.py
from typing import Optional, Dict, Any, Tuple
from datetime import datetime, timedelta, timezone

from ..core.supabase_client import supabase


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
        return account_id.strip() or None

    if not provider or not provider_user_id:
        return None

    r = (
        supabase()
        .table("accounts")
        .select("id")
        .eq("provider", provider)
        .eq("provider_user_id", provider_user_id)
        .limit(1)
        .execute()
    )
    rows = r.data or []
    return rows[0]["id"] if rows else None


def _get_active_subscription_row(account_id: str) -> Optional[Dict[str, Any]]:
    r = (
        supabase()
        .table("user_subscriptions")
        .select("*")
        .eq("account_id", account_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    rows = r.data or []
    return rows[0] if rows else None


def _get_plan_row(plan_code: str) -> Optional[Dict[str, Any]]:
    r = (
        supabase()
        .table("plans")
        .select("*")
        .eq("plan_code", plan_code)
        .eq("active", True)
        .limit(1)
        .execute()
    )
    rows = r.data or []
    return rows[0] if rows else None


def _build_expiry_from_plan(plan: Dict[str, Any]) -> datetime:
    days = int(plan.get("duration_days") or 0)
    if days <= 0:
        # safe fallback: 30 days
        days = 30
    return _now_utc() + timedelta(days=days)


# -----------------------------
# Public API used by routes
# -----------------------------
def get_subscription_status(
    account_id: Optional[str] = None,
    provider: Optional[str] = None,
    provider_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Returns:
      {
        account_id, active, expires_at, grace_until, plan_code, reason, state
      }
    state: "none" | "active" | "grace" | "expired"
    """
    acc_id = _find_account_id(account_id, provider, provider_user_id)
    if not acc_id:
        return {
            "account_id": None,
            "active": False,
            "expires_at": None,
            "grace_until": None,
            "plan_code": None,
            "reason": "no_account",
            "state": "none",
        }

    row = _get_active_subscription_row(acc_id)
    if not row:
        return {
            "account_id": acc_id,
            "active": False,
            "expires_at": None,
            "grace_until": None,
            "plan_code": None,
            "reason": "no_subscription",
            "state": "none",
        }

    now = _now_utc()
    expires_at = _parse_iso(row.get("expires_at") or "")
    grace_until = _parse_iso(row.get("grace_until") or "")

    if expires_at and expires_at > now:
        return {
            "account_id": acc_id,
            "active": True,
            "expires_at": _iso(expires_at),
            "grace_until": _iso(grace_until) if grace_until else None,
            "plan_code": row.get("plan_code"),
            "reason": "active",
            "state": "active",
        }

    # expired
    if grace_until and grace_until > now:
        return {
            "account_id": acc_id,
            "active": True,
            "expires_at": _iso(expires_at) if expires_at else None,
            "grace_until": _iso(grace_until),
            "plan_code": row.get("plan_code"),
            "reason": "grace",
            "state": "grace",
        }

    return {
        "account_id": acc_id,
        "active": False,
        "expires_at": _iso(expires_at) if expires_at else None,
        "grace_until": _iso(grace_until) if grace_until else None,
        "plan_code": row.get("plan_code"),
        "reason": "expired",
        "state": "expired",
    }


def activate_subscription_now(
    account_id: str,
    plan_code: str,
    source: str = "manual",
    reference: Optional[str] = None,
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """
    Creates/updates subscription immediately.
    """
    plan = _get_plan_row(plan_code)
    if not plan:
        return False, "invalid_plan", None

    expires = _build_expiry_from_plan(plan)

    payload = {
        "account_id": account_id,
        "plan_code": plan_code,
        "status": "active",
        "source": source,
        "reference": reference,
        "started_at": _iso(_now_utc()),
        "expires_at": _iso(expires),
        "grace_until": None,
        "updated_at": _iso(_now_utc()),
    }

    r = supabase().table("user_subscriptions").insert(payload).execute()
    row = (r.data or [None])[0]
    return True, "ok", row


def schedule_plan_change_at_expiry(
    account_id: str,
    next_plan_code: str,
    effective_at_iso: str,
) -> Tuple[bool, str]:
    """
    Store a scheduled plan change.
    You can implement as a row in a table, or a column on subscription row.
    Here we store on latest subscription row for simplicity.
    """
    acc_row = _get_active_subscription_row(account_id)
    if not acc_row:
        return False, "no_subscription"

    plan = _get_plan_row(next_plan_code)
    if not plan:
        return False, "invalid_plan"

    upd = {
        "next_plan_code": next_plan_code,
        "next_plan_effective_at": effective_at_iso,
        "updated_at": _iso(_now_utc()),
    }

    supabase().table("user_subscriptions").update(upd).eq("id", acc_row["id"]).execute()
    return True, "ok"


def start_trial_if_eligible(account_id: str) -> Tuple[bool, str]:
    """
    Optional: start a trial if user has no subscription.
    If you don't want trials, keep it returning (False, "disabled").
    """
    row = _get_active_subscription_row(account_id)
    if row:
        return False, "already_has_subscription"

    # If you want a trial plan, create a plan_code like "trial_7d" in plans table.
    trial_plan_code = "trial_7d"
    plan = _get_plan_row(trial_plan_code)
    if not plan:
        return False, "trial_plan_not_configured"

    ok, msg, _ = activate_subscription_now(account_id, trial_plan_code, source="trial")
    return ok, msg


def manual_activate_subscription(account_id: str, plan_code: str) -> Tuple[bool, str]:
    ok, msg, _ = activate_subscription_now(account_id, plan_code, source="manual")
    return ok, msg
