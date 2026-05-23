from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from app.core.supabase_client import supabase
from app.services.plans_service import get_plan


def _sb():
    return supabase() if callable(supabase) else supabase


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _clip(v: Any, n: int = 300) -> str:
    s = str(v or "")
    return s if len(s) <= n else s[:n] + "...<truncated>"


def _safe_dt(v: Any) -> Optional[datetime]:
    try:
        if not v:
            return None
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _normalize_bool(v: Any) -> Optional[bool]:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    s = str(v).strip().lower()
    if s in {"1", "true", "yes", "y", "on", "active"}:
        return True
    if s in {"0", "false", "no", "n", "off", "inactive"}:
        return False
    return bool(v)


def _normalize_sub_row(row: Dict[str, Any]) -> Dict[str, Any]:
    raw_is_active = row.get("is_active")
    return {
        "id": row.get("id"),
        "account_id": row.get("account_id"),
        "plan_code": (row.get("plan_code") or "").strip().lower() or None,
        "status": (row.get("status") or "").strip().lower(),
        "is_active": _normalize_bool(raw_is_active),
        "started_at": row.get("started_at"),
        "expires_at": row.get("expires_at"),
        "trial_until": row.get("trial_until"),
        "grace_until": row.get("grace_until"),
        "current_period_end": row.get("current_period_end"),
        "pending_plan_code": (row.get("pending_plan_code") or "").strip().lower() or None,
        "pending_starts_at": row.get("pending_starts_at"),
        "provider": row.get("provider"),
        "provider_ref": row.get("provider_ref"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _get_subscription_row(account_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    account_id = (account_id or "").strip()
    if not account_id:
        return None, {
            "ok": False,
            "error": "account_id_required",
            "root_cause": "missing_account_id",
            "fix": "Pass canonical account_id to the subscription guard.",
        }

    try:
        res = (
            _sb()
            .table("user_subscriptions")
            .select("*")
            .eq("account_id", account_id)
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        if not rows:
            return None, None
        return _normalize_sub_row(rows[0] or {}), None
    except Exception as e:
        return None, {
            "ok": False,
            "error": "subscription_lookup_failed",
            "root_cause": f"{type(e).__name__}: {_clip(e)}",
            "fix": "Check user_subscriptions table access and Supabase connectivity.",
            "details": {"account_id": account_id},
        }


def _expiry_dt(sub: Optional[Dict[str, Any]]) -> Optional[datetime]:
    if not sub:
        return None
    return _safe_dt(sub.get("expires_at") or sub.get("current_period_end"))


def _subscription_is_active_now(sub: Optional[Dict[str, Any]]) -> bool:
    if not sub:
        return False

    status = str(sub.get("status") or "").strip().lower()
    raw_is_active = sub.get("is_active")
    is_active = True if raw_is_active is None else bool(raw_is_active)

    expires_at = _expiry_dt(sub)
    grace_until = _safe_dt(sub.get("grace_until"))
    now = _now_utc()

    if status == "trial":
        trial_until = _safe_dt(sub.get("trial_until"))
        return bool(trial_until and now < trial_until)

    if status in {"grace", "past_due"}:
        return bool(grace_until and now < grace_until)

    if status in {"cancelled", "canceled"}:
        return bool(expires_at and now < expires_at)

    if status and status != "active":
        return False

    if not is_active:
        return False

    if expires_at and now < expires_at:
        return True

    if grace_until and now < grace_until:
        return True

    # If the row is explicitly active and no expiry column exists, keep access enabled.
    return expires_at is None


def _build_access(sub: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not sub:
        return {
            "allowed": False,
            "reason": "no_subscription",
            "status": "none",
            "upgrade_required": True,
        }

    now = _now_utc()
    expires_at = _expiry_dt(sub)
    trial_until = _safe_dt(sub.get("trial_until"))
    grace_until = _safe_dt(sub.get("grace_until"))

    status = (sub.get("status") or "").strip().lower()
    raw_is_active = sub.get("is_active")
    is_active = True if raw_is_active is None else bool(raw_is_active)

    allowed = False
    reason = "inactive_subscription"

    if is_active and (status == "active" or not status):
        if expires_at is None or now < expires_at:
            allowed = True
            reason = "active"
        else:
            allowed = False
            reason = "expired"
    elif status == "trial":
        if trial_until and now < trial_until:
            allowed = True
            reason = "trial"
        else:
            allowed = False
            reason = "trial_expired"
    elif status in {"grace", "past_due"}:
        if grace_until and now < grace_until:
            allowed = True
            reason = "grace"
        else:
            allowed = False
            reason = "grace_expired"
    elif status == "expired":
        allowed = False
        reason = "expired"
    elif status == "inactive":
        allowed = False
        reason = "inactive"
    elif status in {"cancelled", "canceled"}:
        if expires_at and now < expires_at:
            allowed = True
            reason = "active_until_period_end"
        else:
            allowed = False
            reason = "cancelled"

    return {
        "allowed": allowed,
        "reason": reason,
        "status": status or ("active" if is_active else "inactive"),
        "upgrade_required": not allowed,
    }


def get_subscription_snapshot(account_id: str) -> Dict[str, Any]:
    account_id = (account_id or "").strip()
    if not account_id:
        return {
            "ok": False,
            "error": "account_id_required",
            "root_cause": "missing_account_id",
            "fix": "Pass canonical account_id to the subscription guard.",
        }

    sub, err = _get_subscription_row(account_id)
    if err:
        return err

    if not sub:
        return {
            "ok": True,
            "account_id": account_id,
            "subscription": None,
            "plan": None,
            "plan_code": None,
            "plan_family": None,
            "daily_answers_limit": 0,
            "ai_credits_total": 0,
            "active_now": False,
            "channel_limits": {
                "max_total_channels": 0,
                "max_whatsapp_channels": 0,
                "max_telegram_channels": 0,
            },
            "user_limits": {
                "max_workspace_users": 0,
                "max_linked_web_accounts": 0,
            },
            "access": {
                "allowed": False,
                "reason": "no_subscription",
                "status": "none",
                "upgrade_required": True,
            },
        }

    access = _build_access(sub)
    active_now = _subscription_is_active_now(sub)

    plan_code = (sub.get("plan_code") or "").strip().lower()
    plan = get_plan(plan_code) if plan_code else None
    channel_limits = {
        "max_total_channels": int((plan or {}).get("max_total_channels") or 0),
        "max_whatsapp_channels": int((plan or {}).get("max_whatsapp_channels") or 0),
        "max_telegram_channels": int((plan or {}).get("max_telegram_channels") or 0),
    }
    user_limits = {
        "max_workspace_users": int((plan or {}).get("max_workspace_users") or 0),
        "max_linked_web_accounts": int((plan or {}).get("max_linked_web_accounts") or 0),
    }

    return {
        "ok": True,
        "account_id": account_id,
        "subscription": sub,
        "plan": plan,
        "plan_code": plan_code or None,
        "plan_family": (plan or {}).get("plan_family") or (plan or {}).get("tier"),
        "daily_answers_limit": int((plan or {}).get("daily_answers_limit") or 0),
        "ai_credits_total": int((plan or {}).get("ai_credits_total") or 0),
        "active_now": active_now,
        "channel_limits": channel_limits,
        "user_limits": user_limits,
        "access": access,
    }


def require_active_subscription(account_id: str) -> Dict[str, Any]:
    snap = get_subscription_snapshot(account_id)
    if not snap.get("ok"):
        return snap

    access = snap.get("access") or {}
    if access.get("allowed"):
        plan_code = (snap.get("plan_code") or "").strip().lower() if snap.get("plan_code") else None
        plan = snap.get("plan")

        if not plan_code:
            return {
                "ok": False,
                "error": "subscription_plan_missing",
                "root_cause": "active_subscription_missing_plan_code",
                "fix": "Repair user_subscriptions.plan_code for this account.",
                "details": {
                    "account_id": account_id,
                    "subscription": snap.get("subscription"),
                },
            }

        if not plan:
            return {
                "ok": False,
                "error": "plan_not_found",
                "root_cause": f"plans row not found for active subscription plan_code={plan_code}",
                "fix": "Insert or repair the matching plan in the plans table.",
                "details": {
                    "account_id": account_id,
                    "plan_code": plan_code,
                    "subscription": snap.get("subscription"),
                },
            }

        if not bool(plan.get("active", True)):
            return {
                "ok": False,
                "error": "plan_inactive",
                "root_cause": f"active subscription points to inactive plan={plan_code}",
                "fix": "Reactivate the plan or migrate the user to another active plan.",
                "details": {
                    "account_id": account_id,
                    "plan_code": plan_code,
                    "subscription": snap.get("subscription"),
                },
            }

        return {
            "ok": True,
            "account_id": account_id,
            "subscription": snap.get("subscription"),
            "access": access,
            "active_now": bool(snap.get("active_now")),
            "plan": plan,
            "plan_code": plan_code,
            "plan_family": snap.get("plan_family"),
            "channel_limits": snap.get("channel_limits") or {},
            "user_limits": snap.get("user_limits") or {},
            "daily_answers_limit": int(snap.get("daily_answers_limit") or 0),
            "ai_credits_total": int(snap.get("ai_credits_total") or 0),
        }

    sub = snap.get("subscription")
    return {
        "ok": False,
        "error": "subscription_required",
        "root_cause": access.get("reason") or "inactive_subscription",
        "fix": "Upgrade or reactivate billing before using paid AI endpoints.",
        "details": {
            "account_id": account_id,
            "subscription": sub,
            "access": access,
            "recommended_action": "upgrade_plan",
        },
    }
