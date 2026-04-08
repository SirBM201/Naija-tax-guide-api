from __future__ import annotations

from typing import Any, Dict, Optional

from app.core.supabase_client import supabase
from app.services.subscription_guard import require_active_subscription


def _sb():
    return supabase() if callable(supabase) else supabase


def _reason_payload(reason: str, *, details: Any = None, fix: Optional[str] = None, root_cause: Optional[str] = None) -> Dict[str, Any]:
    payload = {"ok": False, "reason": reason, "error": reason}
    if details is not None:
        payload["details"] = details
    if fix:
        payload["fix"] = fix
    if root_cause:
        payload["root_cause"] = root_cause
    return payload


def get_account_entitlements(account_id: str) -> Dict[str, Any]:
    sub = require_active_subscription(account_id)
    if not sub.get("ok"):
        return sub

    plan = sub.get("plan") or {}
    channel_limits = sub.get("channel_limits") or {}

    max_workspace_users = int(plan.get("max_workspace_users") or plan.get("max_linked_web_accounts") or 1)
    max_linked_web_accounts = int(plan.get("max_linked_web_accounts") or max_workspace_users)

    return {
        "ok": True,
        "account_id": account_id,
        "plan_code": sub.get("plan_code"),
        "plan_family": sub.get("plan_family"),
        "channel_limits": channel_limits,
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
    max_workspace_users = int(limits.get("max_workspace_users") or 1)

    counts = count_workspace_members(owner_account_id)
    current_total = int(counts.get("owner_included_total") or 1)

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
