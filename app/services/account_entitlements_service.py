from __future__ import annotations

from typing import Any, Dict

from app.core.supabase_client import supabase
from app.services.subscription_guard import require_active_subscription


def _sb():
    return supabase() if callable(supabase) else supabase


def _clip(value: Any, n: int = 240) -> str:
    s = str(value or "")
    return s if len(s) <= n else s[:n] + "…"


def get_workspace_user_counts(owner_account_id: str) -> Dict[str, int]:
    """
    Current repo does not yet have a dedicated workspace_members table.
    So current enforcement is grounded on canonical accounts.account_id presence only.

    This makes the entitlement layer real now:
    - Starter => only 1 linked web account/workspace user
    - Professional/Business => future-ready values already exposed

    Once a workspace/team table is added, only this function needs expansion.
    """
    owner_account_id = str(owner_account_id or "").strip()
    if not owner_account_id:
        return {"workspace_users": 0, "linked_web_accounts": 0}

    try:
        res = (
            _sb()
            .table("accounts")
            .select("account_id,provider")
            .eq("account_id", owner_account_id)
            .eq("provider", "web")
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        count = 1 if rows else 0
        return {
            "workspace_users": count,
            "linked_web_accounts": count,
        }
    except Exception:
        return {"workspace_users": 0, "linked_web_accounts": 0}


def enforce_user_entitlements(owner_account_id: str) -> Dict[str, Any]:
    sub = require_active_subscription(owner_account_id)
    if not sub.get("ok"):
        return {
            "ok": False,
            "error": "subscription_required_for_user_entitlements",
            "root_cause": str(sub.get("root_cause") or sub.get("error") or ""),
            "fix": "Activate a paid plan before adding extra linked users or linked web accounts.",
            "details": sub,
        }

    user_limits = sub.get("user_limits") or {}
    max_workspace_users = int(user_limits.get("max_workspace_users") or 0)
    max_linked_web_accounts = int(user_limits.get("max_linked_web_accounts") or 0)
    counts = get_workspace_user_counts(owner_account_id)

    workspace_users = int(counts.get("workspace_users") or 0)
    linked_web_accounts = int(counts.get("linked_web_accounts") or 0)

    blocked = False
    reasons = []

    if max_workspace_users > 0 and workspace_users >= max_workspace_users:
        blocked = True
        reasons.append("workspace_user_limit_reached")

    if max_linked_web_accounts > 0 and linked_web_accounts >= max_linked_web_accounts:
        blocked = True
        reasons.append("linked_web_account_limit_reached")

    return {
        "ok": not blocked,
        "blocked": blocked,
        "reasons": reasons,
        "plan_code": sub.get("plan_code"),
        "plan_family": sub.get("plan_family"),
        "counts": counts,
        "limits": user_limits,
        "fix": "Upgrade plan before adding more linked users/accounts." if blocked else None,
    }
