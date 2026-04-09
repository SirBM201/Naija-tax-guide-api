from __future__ import annotations

from typing import Any, Dict, Optional

from app.core.supabase_client import supabase
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


def _free_entitlements(account_id: str, sub_payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Return a safe default entitlement payload for accounts without an active subscription.

    This keeps workspace endpoints stable for free/unpaid users instead of returning
    a hard 400 response from /api/workspace/limits.
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
        "description": "Default free access with owner-only workspace access.",
        "max_workspace_users": 1,
        "max_linked_web_accounts": 1,
        "max_total_channels": 0,
        "max_whatsapp_channels": 0,
        "max_telegram_channels": 0,
    }

    return {
        "ok": True,
        "account_id": account_id,
        "plan_code": None,
        "plan_family": "free",
        "channel_limits": {
            "max_total_channels": 0,
            "max_whatsapp_channels": 0,
            "max_telegram_channels": 0,
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
    sub = require_active_subscription(account_id)

    # Important: no active subscription should not break workspace limits endpoint.
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
