from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any

GRACE_DAYS = 3


def _now():
    return datetime.now(timezone.utc)


def _compute_grace(end_at: Optional[datetime]) -> Optional[datetime]:
    if not end_at:
        return None
    return end_at + timedelta(days=GRACE_DAYS)


def _derive_access_state(status: str, end_at: Optional[datetime]) -> Dict[str, Any]:
    now = _now()
    grace_until = _compute_grace(end_at)

    is_within_period = end_at and end_at > now
    is_within_grace = grace_until and grace_until > now

    access = False

    if status in ["active", "trial"] and is_within_period:
        access = True

    elif status == "past_due" and is_within_grace:
        access = True

    elif status == "cancelled" and is_within_period:
        access = True

    return {
        "active": access,
        "grace_until": grace_until,
        "state": status,
    }


async def get_subscription_status(supabase, user_id: str) -> Dict[str, Any]:
    """
    Global-standard subscription resolver.
    Works with multi-row subscription history.
    """

    if not user_id:
        return {
            "account_id": None,
            "active": False,
            "expires_at": None,
            "grace_until": None,
            "plan_code": None,
            "reason": "no_user",
            "state": "none",
        }

    # Fetch latest subscription row
    result = (
        supabase.table("subscriptions")
        .select("*")
        .eq("user_id", user_id)
        .order("end_at", desc=True)
        .limit(1)
        .execute()
    )

    rows = result.data or []

    if not rows:
        return {
            "account_id": user_id,
            "active": False,
            "expires_at": None,
            "grace_until": None,
            "plan_code": None,
            "reason": "no_subscription",
            "state": "none",
        }

    sub = rows[0]

    status = sub.get("status")
    end_at = sub.get("end_at")
    plan = sub.get("plan")

    # Convert ISO string → datetime
    if end_at and isinstance(end_at, str):
        end_at = datetime.fromisoformat(end_at.replace("Z", "+00:00"))

    derived = _derive_access_state(status, end_at)

    return {
        "account_id": user_id,
        "active": derived["active"],
        "expires_at": end_at,
        "grace_until": derived["grace_until"],
        "plan_code": plan,
        "reason": None if derived["active"] else status,
        "state": status,
    }
