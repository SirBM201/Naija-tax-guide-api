from __future__ import annotations

from typing import Any, Dict

from app.core.supabase_client import supabase
from app.services.account_entitlements_service import get_account_entitlements


def _sb():
    return supabase() if callable(supabase) else supabase


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _normalize_channel(channel_type: Any) -> str:
    value = _clean(channel_type).lower()
    if value in {"wa", "whatsapp", "waba"}:
        return "whatsapp"
    if value in {"tg", "telegram"}:
        return "telegram"
    return value


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _row_active(row: Dict[str, Any]) -> bool:
    if row.get("is_active") is False:
        return False
    return _clean(row.get("status")).lower() not in {"paused", "inactive", "disabled", "disconnected", "deleted"}


def count_linked_channels(account_id: str) -> Dict[str, int]:
    """Count durable connections. Paused connections remain linked by design."""
    counts = {"total": 0, "whatsapp": 0, "telegram": 0}
    account_id = _clean(account_id)
    if not account_id:
        return counts
    try:
        res = _sb().table("channel_identities").select("channel_type").eq("account_id", account_id).execute()
        rows = getattr(res, "data", None) or []
    except Exception:
        rows = []
    seen = set()
    for row in rows:
        channel = _normalize_channel((row or {}).get("channel_type"))
        if channel not in {"whatsapp", "telegram"} or channel in seen:
            continue
        seen.add(channel)
        counts[channel] += 1
        counts["total"] += 1
    return counts


def count_active_channels(account_id: str) -> Dict[str, int]:
    counts = {"total": 0, "whatsapp": 0, "telegram": 0}
    account_id = _clean(account_id)
    if not account_id:
        return counts
    try:
        res = _sb().table("channel_identities").select("channel_type,is_active,status").eq("account_id", account_id).execute()
        rows = getattr(res, "data", None) or []
    except Exception:
        rows = []
    seen = set()
    for row in rows:
        row = row or {}
        channel = _normalize_channel(row.get("channel_type"))
        if channel not in {"whatsapp", "telegram"} or channel in seen or not _row_active(row):
            continue
        seen.add(channel)
        counts[channel] += 1
        counts["total"] += 1
    return counts


def enforce_channel_link_limit(account_id: str, channel_type: str) -> Dict[str, Any]:
    account_id = _clean(account_id)
    channel = _normalize_channel(channel_type)
    if not account_id:
        return {"ok": False, "error": "account_id_required"}
    if channel not in {"whatsapp", "telegram"}:
        return {"ok": False, "error": "unsupported_channel", "channel_type": channel}

    entitlements = get_account_entitlements(account_id)
    if not entitlements.get("ok"):
        return entitlements
    limits = entitlements.get("channel_limits") or {}
    linked = count_linked_channels(account_id)
    active = count_active_channels(account_id)

    # A durable existing connection is never deleted merely because the plan changed.
    # Relinking it is allowed; activation remains governed by the active-channel limit.
    if linked.get(channel, 0) > 0:
        return {
            "ok": True, "already_linked": True, "account_id": account_id,
            "channel_type": channel, "counts": linked, "active_counts": active,
            "limits": limits, "plan_code": entitlements.get("plan_code"),
            "plan_family": entitlements.get("plan_family"),
            "selection_required": active["total"] > _to_int(limits.get("max_total_channels"), 0),
        }

    max_total = _to_int(limits.get("max_total_channels"), 0)
    max_channel = _to_int(limits.get(f"max_{channel}_channels"), 0)
    if max_total <= 0 or active["total"] >= max_total:
        return {
            "ok": False, "error": "channel_limit_reached", "reason": "total_active_channel_limit_reached",
            "upgrade_required": True, "account_id": account_id, "channel_type": channel,
            "counts": linked, "active_counts": active, "limits": limits,
            "plan_code": entitlements.get("plan_code"), "plan_family": entitlements.get("plan_family"),
        }
    if max_channel <= 0 or active[channel] >= max_channel:
        return {
            "ok": False, "error": "channel_limit_reached", "reason": f"{channel}_active_channel_limit_reached",
            "upgrade_required": True, "account_id": account_id, "channel_type": channel,
            "counts": linked, "active_counts": active, "limits": limits,
            "plan_code": entitlements.get("plan_code"), "plan_family": entitlements.get("plan_family"),
        }
    return {
        "ok": True, "already_linked": False, "account_id": account_id,
        "channel_type": channel, "counts": linked, "active_counts": active, "limits": limits,
        "plan_code": entitlements.get("plan_code"), "plan_family": entitlements.get("plan_family"),
    }
