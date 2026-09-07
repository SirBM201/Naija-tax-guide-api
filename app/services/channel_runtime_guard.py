from __future__ import annotations

from typing import Any, Dict, Optional

from app.core.supabase_client import supabase
from app.services.channel_activation_service import get_channel_activation_snapshot

SERVICE_VERSION = "2026-09-07-v1-runtime-channel-guard"


def _sb():
    return supabase() if callable(supabase) else supabase


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _identity(channel_type: str, provider_user_id: str) -> Optional[Dict[str, Any]]:
    channel_type = _clean(channel_type).lower()
    provider_user_id = _clean(provider_user_id)
    if not channel_type or not provider_user_id:
        return None
    try:
        res = (
            _sb().table("channel_identities")
            .select("*")
            .eq("channel_type", channel_type)
            .eq("provider_user_id", provider_user_id)
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        return rows[0] if rows else None
    except Exception:
        return None


def check_channel_runtime_access(*, account_id: str, channel_type: str, provider_user_id: str) -> Dict[str, Any]:
    """Gate a durable channel connection before it consumes NTG services.

    Unlinked/legacy users are not blocked here because linking flows still need to
    work. Once a durable channel_identity exists, paused state is authoritative.
    Accounts above their current active-channel entitlement are also held until
    the owner explicitly chooses active channels in the web Channels workspace.
    """
    account_id = _clean(account_id)
    channel_type = _clean(channel_type).lower()
    provider_user_id = _clean(provider_user_id)
    identity = _identity(channel_type, provider_user_id)

    if not identity:
        return {"ok": True, "allowed": True, "linked": False, "reason": "no_durable_identity", "version": SERVICE_VERSION}

    identity_account = _clean(identity.get("account_id"))
    if account_id and identity_account and identity_account != account_id:
        return {"ok": False, "allowed": False, "linked": True, "reason": "identity_account_mismatch", "version": SERVICE_VERSION}

    status = _clean(identity.get("status")).lower()
    is_active = identity.get("is_active")
    paused = status in {"paused", "inactive", "disabled", "suspended"} or is_active is False
    if paused:
        return {
            "ok": True,
            "allowed": False,
            "linked": True,
            "paused": True,
            "reason": "channel_paused",
            "message": "This channel is still connected, but it is paused under your current plan. Open the Channels page on naijataxguides.com to choose which connected channel should remain active.",
            "version": SERVICE_VERSION,
        }

    target_account = account_id or identity_account
    if target_account:
        snapshot = get_channel_activation_snapshot(target_account)
        if snapshot.get("ok") and snapshot.get("selection_required"):
            return {
                "ok": True,
                "allowed": False,
                "linked": True,
                "paused": False,
                "selection_required": True,
                "reason": "active_channel_selection_required",
                "message": "Your account has more active external channels than your current plan allows. Open the Channels page on naijataxguides.com and choose the channel(s) you want to keep active. Your other connections will be preserved and paused, not deleted.",
                "version": SERVICE_VERSION,
            }

    return {"ok": True, "allowed": True, "linked": True, "paused": False, "reason": "active", "version": SERVICE_VERSION}
