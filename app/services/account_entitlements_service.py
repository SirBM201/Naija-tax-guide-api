from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.core.supabase_client import supabase
from app.services.plans_service import get_plan
from app.services.subscription_guard import require_active_subscription


def _sb():
    return supabase() if callable(supabase) else supabase


def _reason_payload(
    reason: str,
    *,
    details: Any = None,
    fix: Optional[str] = None,
    root_cause: Optional[str] = None,
) -> Dict[str, Any]:
    payload = {"ok": False, "reason": reason, "error": reason}
    if details is not None:
        payload["details"] = details
    if fix:
        payload["fix"] = fix
    if root_cause:
        payload["root_cause"] = root_cause
    return payload


def _to_int(value: Any, default: int) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except Exception:
        return default


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _lower(value: Any) -> str:
    return _clean(value).lower()


def _safe_dt(value: Any) -> datetime:
    try:
        if not value:
            return datetime.min.replace(tzinfo=timezone.utc)
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


def _normalize_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on", "active", "paid"}:
        return True
    if text in {"0", "false", "no", "n", "off", "inactive", "expired"}:
        return False
    return default


def _subscription_expiry(row: Dict[str, Any]) -> Any:
    return (
        row.get("expires_at")
        or row.get("current_period_end")
        or row.get("ends_at")
        or row.get("period_end")
        or row.get("grace_until")
        or row.get("trial_until")
    )


def _subscription_is_active(row: Dict[str, Any]) -> bool:
    status = _lower(row.get("status"))
    if status in {"inactive", "expired", "cancelled", "canceled", "disabled", "paused", "failed"}:
        return False

    if not _normalize_bool(row.get("is_active"), True):
        return False

    expiry = _safe_dt(_subscription_expiry(row))
    if expiry != datetime.min.replace(tzinfo=timezone.utc) and expiry <= datetime.now(timezone.utc):
        return False

    return status in {"", "active", "trial", "grace", "past_due", "paid"} or bool(_subscription_expiry(row))


def _paid_plan_code(row: Dict[str, Any]) -> str:
    code = _lower(row.get("plan_code"))
    return "" if code in {"", "free", "free_forever"} else code


def _row_sort_key(row: Dict[str, Any]) -> tuple:
    return (
        1 if _subscription_is_active(row) else 0,
        1 if _paid_plan_code(row) else 0,
        _safe_dt(row.get("updated_at")),
        _safe_dt(row.get("created_at")),
        _safe_dt(_subscription_expiry(row)),
    )


def _latest_paid_subscription(account_id: str) -> Optional[Dict[str, Any]]:
    account_id = _clean(account_id)
    if not account_id:
        return None

    try:
        query = _sb().table("user_subscriptions").select("*").eq("account_id", account_id)
        try:
            query = query.order("updated_at", desc=True)
        except Exception:
            pass
        res = query.limit(50).execute()
        rows = [r for r in (getattr(res, "data", None) or []) if isinstance(r, dict)]
    except Exception:
        return None

    if not rows:
        return None

    ranked = sorted(rows, key=_row_sort_key, reverse=True)
    for row in ranked:
        if _paid_plan_code(row) and _subscription_is_active(row):
            return row
    return ranked[0] if ranked and _paid_plan_code(ranked[0]) else None


def _entitlements_from_subscription(account_id: str, row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    plan_code = _paid_plan_code(row)
    if not plan_code:
        return None

    plan = get_plan(plan_code)
    if not plan:
        return None

    channel_limits = {
        "max_total_channels": _to_int(plan.get("max_total_channels"), 0),
        "max_whatsapp_channels": _to_int(plan.get("max_whatsapp_channels"), 0),
        "max_telegram_channels": _to_int(plan.get("max_telegram_channels"), 0),
    }
    workspace_limits = {
        "max_workspace_users": _to_int(
            plan.get("max_workspace_users") or plan.get("max_linked_web_accounts"),
            1,
        ),
        "max_linked_web_accounts": _to_int(
            plan.get("max_linked_web_accounts") or plan.get("max_workspace_users"),
            1,
        ),
    }

    return {
        "ok": True,
        "account_id": account_id,
        "plan_code": plan_code,
        "plan_family": plan.get("plan_family") or plan.get("tier"),
        "channel_limits": channel_limits,
        "workspace_limits": workspace_limits,
        "subscription": row,
        "plan": plan,
        "access_mode": "latest_paid_subscription",
    }


def _free_entitlements(
    account_id: str,
    sub_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Return a safe default entitlement payload for accounts without an active subscription.

    Free users are allowed one total channel, and they may choose either
    WhatsApp or Telegram. They still remain limited to owner-only workspace access.
    """
    reason = (sub_payload or {}).get("reason") or "no_active_subscription"

    plan = {
        "name": "Free",
        "code": "free",
        "plan_family": "free",
        "tier": "free",
        "active": False,
        "currency": "NGN",
        "cycle": None,
        "price": 0,
        "description": "Default free access with owner-only workspace access and one user-selected channel.",
        "max_workspace_users": 1,
        "max_linked_web_accounts": 1,
        "max_total_channels": 1,
        "max_whatsapp_channels": 1,
        "max_telegram_channels": 1,
    }

    return {
        "ok": True,
        "account_id": account_id,
        "plan_code": "free",
        "plan_family": "free",
        "channel_limits": {
            "max_total_channels": 1,
            "max_whatsapp_channels": 1,
            "max_telegram_channels": 1,
        },
        "workspace_limits": {
            "max_workspace_users": 1,
            "max_linked_web_accounts": 1,
        },
        "subscription": None,
        "plan": plan,
        "inactive_reason": reason,
        "access_mode": "free_fallback",
    }


def get_account_entitlements(account_id: str) -> Dict[str, Any]:
    latest_paid = _latest_paid_subscription(account_id)
    paid_entitlements = _entitlements_from_subscription(account_id, latest_paid or {}) if latest_paid else None
    if paid_entitlements:
        return paid_entitlements

    sub = require_active_subscription(account_id)

    # No active subscription should not break workspace or channel limits endpoints.
    # Instead, return the free fallback entitlement payload.
    if not sub.get("ok"):
        return _free_entitlements(account_id, sub)

    plan = sub.get("plan") or {}
    channel_limits = sub.get("channel_limits") or {}

    max_workspace_users = _to_int(
        plan.get("max_workspace_users") or plan.get("max_linked_web_accounts"),
        1,
    )
    max_linked_web_accounts = _to_int(
        plan.get("max_linked_web_accounts") or max_workspace_users,
        max_workspace_users,
    )

    return {
        "ok": True,
        "account_id": account_id,
        "plan_code": sub.get("plan_code"),
        "plan_family": sub.get("plan_family"),
        "channel_limits": {
            "max_total_channels": _to_int(channel_limits.get("max_total_channels"), 0),
            "max_whatsapp_channels": _to_int(channel_limits.get("max_whatsapp_channels"), 0),
            "max_telegram_channels": _to_int(channel_limits.get("max_telegram_channels"), 0),
        },
        "workspace_limits": {
            "max_workspace_users": max_workspace_users,
            "max_linked_web_accounts": max_linked_web_accounts,
        },
        "subscription": sub.get("subscription"),
        "plan": plan,
        "access_mode": "subscription_guard",
    }


def count_workspace_members(owner_account_id: str) -> Dict[str, int]:
    counts = {
        "owner_included_total": 1,
        "active_members_only": 0,
    }
    try:
        res = (
            _sb()
            .table("workspace_members")
            .select("member_account_id,status")
            .eq("owner_account_id", owner_account_id)
            .execute()
        )
        rows = getattr(res, "data", None) or []
    except Exception:
        return counts

    active_rows = []
    for row in rows:
        status = str((row or {}).get("status") or "").strip().lower()
        if status in {"active", "invited"}:
            active_rows.append(row)

    counts["active_members_only"] = len(active_rows)
    counts["owner_included_total"] = 1 + len(active_rows)
    return counts


def enforce_workspace_member_limit(owner_account_id: str) -> Dict[str, Any]:
    ent = get_account_entitlements(owner_account_id)
    if not ent.get("ok"):
        return ent

    limits = ent.get("workspace_limits") or {}
    max_workspace_users = _to_int(limits.get("max_workspace_users"), 1)

    counts = count_workspace_members(owner_account_id)
    current_total = _to_int(counts.get("owner_included_total"), 1)

    if max_workspace_users > 0 and current_total >= max_workspace_users:
        return _reason_payload(
            "workspace_member_limit_reached",
            details={
                "owner_account_id": owner_account_id,
                "plan_code": ent.get("plan_code"),
                "plan_family": ent.get("plan_family"),
                "counts": counts,
                "limits": limits,
            },
            fix="Remove an existing workspace member or upgrade to a higher plan.",
        )

    return {
        "ok": True,
        "owner_account_id": owner_account_id,
        "plan_code": ent.get("plan_code"),
        "plan_family": ent.get("plan_family"),
        "counts": counts,
        "limits": limits,
    }
