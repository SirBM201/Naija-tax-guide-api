from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.core.supabase_client import supabase
from app.services.account_entitlements_service import get_account_entitlements

SERVICE_VERSION = "2026-09-07-v1-preserve-links-pause-excess"
SUPPORTED_CHANNELS = {"whatsapp", "telegram"}


def _sb():
    return supabase() if callable(supabase) else supabase


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _normalize_channel(value: Any) -> str:
    value = _clean(value).lower()
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


def _is_active(row: Dict[str, Any]) -> bool:
    if row.get("is_active") is False:
        return False
    status = _clean(row.get("status")).lower()
    return status not in {"paused", "inactive", "disabled", "disconnected", "deleted"}


def _rows(account_id: str) -> List[Dict[str, Any]]:
    try:
        res = (
            _sb().table("channel_identities").select("*")
            .eq("account_id", _clean(account_id)).execute()
        )
        return [r for r in (getattr(res, "data", None) or []) if isinstance(r, dict)]
    except Exception:
        return []


def get_channel_activation_snapshot(account_id: str) -> Dict[str, Any]:
    account_id = _clean(account_id)
    ent = get_account_entitlements(account_id)
    if not ent.get("ok"):
        return ent

    limits = ent.get("channel_limits") or {}
    max_total = _to_int(limits.get("max_total_channels"), 0)
    rows = [r for r in _rows(account_id) if _normalize_channel(r.get("channel_type")) in SUPPORTED_CHANNELS]
    connected = []
    active = []
    paused = []
    seen = set()
    for row in rows:
        channel = _normalize_channel(row.get("channel_type"))
        if channel in seen:
            continue
        seen.add(channel)
        connected.append(channel)
        (active if _is_active(row) else paused).append(channel)

    return {
        "ok": True,
        "service_version": SERVICE_VERSION,
        "account_id": account_id,
        "plan_code": ent.get("plan_code"),
        "limits": limits,
        "connected_channels": connected,
        "active_channels": active,
        "paused_channels": paused,
        "connected_count": len(connected),
        "active_count": len(active),
        "over_active_limit": len(active) > max_total,
        "selection_required": len(active) > max_total,
        "max_active_channels": max_total,
        "policy": "connections_preserved_excess_paused",
    }


def select_active_channels(account_id: str, channels: List[str]) -> Dict[str, Any]:
    """Apply the user's explicit active-channel selection without deleting connections."""
    account_id = _clean(account_id)
    requested = []
    for raw in channels or []:
        channel = _normalize_channel(raw)
        if channel in SUPPORTED_CHANNELS and channel not in requested:
            requested.append(channel)

    ent = get_account_entitlements(account_id)
    if not ent.get("ok"):
        return ent
    limits = ent.get("channel_limits") or {}
    max_total = _to_int(limits.get("max_total_channels"), 0)
    if len(requested) > max_total:
        return {"ok": False, "error": "active_channel_limit_exceeded", "max_active_channels": max_total}

    rows = _rows(account_id)
    connected = {_normalize_channel(r.get("channel_type")) for r in rows}
    missing = [c for c in requested if c not in connected]
    if missing:
        return {"ok": False, "error": "channel_not_connected", "channels": missing}

    now = datetime.now(timezone.utc).isoformat()
    changed = []
    for row in rows:
        row_id = row.get("id")
        channel = _normalize_channel(row.get("channel_type"))
        if not row_id or channel not in SUPPORTED_CHANNELS:
            continue
        should_activate = channel in requested
        payload = {
            "is_active": should_activate,
            "status": "active" if should_activate else "paused",
        }
        # Do not require optional timestamp columns; preserve schema compatibility.
        try:
            _sb().table("channel_identities").update(payload).eq("id", row_id).execute()
            changed.append({"channel_type": channel, "active": should_activate})
        except Exception as exc:
            return {
                "ok": False,
                "error": "channel_activation_update_failed",
                "channel_type": channel,
                "detail": f"{type(exc).__name__}: {exc}",
            }

    return {
        "ok": True,
        "service_version": SERVICE_VERSION,
        "account_id": account_id,
        "active_channels": requested,
        "max_active_channels": max_total,
        "changed": changed,
        "connections_deleted": False,
        "updated_at": now,
    }


def reconcile_after_plan_change(account_id: str) -> Dict[str, Any]:
    """Detect over-limit state after downgrade. Never chooses for the user and never deletes."""
    snap = get_channel_activation_snapshot(account_id)
    if not snap.get("ok"):
        return snap
    if snap.get("selection_required"):
        return {
            **snap,
            "action_required": "choose_active_channels",
            "message": "Your new plan supports fewer active messaging channels. Choose which connected channel(s) to keep active; the rest will be paused, not deleted.",
        }
    return {**snap, "action_required": None}
