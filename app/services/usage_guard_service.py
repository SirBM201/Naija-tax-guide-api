from __future__ import annotations

from typing import Any, Dict, Optional

from app.repositories.monthly_usage_repo import (
    get_account_monthly_ai_limit,
    get_monthly_ai_usage,
)
from app.services.credits_service import get_credit_balance_details
from app.services.subscription_guard import get_subscription_snapshot


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def get_ai_usage_state(account_id: str) -> Dict[str, Any]:
    """
    Unified AI-usage guard.

    IMPORTANT:
    The app currently displays visible credits from ai_credit_balances.
    Therefore ask-flow permission must not rely only on monthly RPC counters.

    Rule:
    - Safe cached answers can still return regardless of fresh AI credits.
    - Fresh AI generation requires:
        1. active subscription access
        2. positive visible credit balance
        3. not exceeding monthly AI limit (if a limit exists)

    This prevents the mismatch where UI shows credits left > 0
    but ask-flow says no fresh AI credits are available.
    """
    account_id = (account_id or "").strip()

    limit_info = get_account_monthly_ai_limit(account_id)
    monthly_ai_usage = _as_int(get_monthly_ai_usage(account_id), 0)
    monthly_ai_limit = _as_int(limit_info.get("monthly_ai_limit"), 0)

    credit_details = get_credit_balance_details(account_id)
    credit_balance = _as_int((credit_details or {}).get("balance"), 0)
    credit_lookup_ok = bool((credit_details or {}).get("ok", False))

    sub_snapshot = get_subscription_snapshot(account_id)
    subscription_ok = bool((sub_snapshot or {}).get("ok", False))
    active_now = bool((sub_snapshot or {}).get("active_now", False))
    access = (sub_snapshot or {}).get("access") or {}
    access_allowed = bool(access.get("allowed", False))

    # If the monthly limit is not configured or <= 0, do not block on it.
    # In that case visible credit balance becomes the main fresh-AI gate.
    within_monthly_limit = True
    monthly_remaining: Optional[int] = None
    if monthly_ai_limit > 0:
        within_monthly_limit = monthly_ai_usage < monthly_ai_limit
        monthly_remaining = max(monthly_ai_limit - monthly_ai_usage, 0)

    has_visible_credit = credit_lookup_ok and credit_balance > 0
    has_subscription_access = subscription_ok and active_now and access_allowed

    has_ai_credit = bool(
        has_subscription_access
        and has_visible_credit
        and within_monthly_limit
    )

    reason = "ok"
    if not has_subscription_access:
        reason = "subscription_inactive"
    elif not has_visible_credit:
        reason = "visible_credit_balance_zero"
    elif not within_monthly_limit:
        reason = "monthly_limit_reached"

    return {
        "account_id": account_id,
        "plan_code": (limit_info.get("plan_code") or sub_snapshot.get("plan_code") or "monthly"),
        "monthly_ai_usage": monthly_ai_usage,
        "monthly_ai_limit": monthly_ai_limit,
        "monthly_ai_remaining": monthly_remaining,
        "credit_balance": credit_balance,
        "credit_lookup_ok": credit_lookup_ok,
        "subscription_ok": subscription_ok,
        "active_now": active_now,
        "access_allowed": access_allowed,
        "has_visible_credit": has_visible_credit,
        "within_monthly_limit": within_monthly_limit,
        "has_ai_credit": has_ai_credit,
        "reason": reason,
        "subscription_access": access,
    }
