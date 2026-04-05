from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.core.supabase_client import supabase
from app.services.accounts_service import (
    find_account_by_provider_user_id,
    mark_channel_claimed_for_auth_user,
    upsert_account_link,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_link_token(provider: str, code: str) -> Optional[Dict[str, Any]]:
    resp = (
        supabase.table("link_tokens")
        .select("*")
        .eq("provider", provider)
        .eq("code", code)
        .limit(1)
        .execute()
    )
    rows = getattr(resp, "data", None) or []
    return rows[0] if rows else None


def _mark_token_used(token_id: str, provider_user_id: str) -> None:
    (
        supabase.table("link_tokens")
        .update(
            {
                "used_at": _now_iso(),
                "used_by_provider_user_id": provider_user_id,
            }
        )
        .eq("id", token_id)
        .execute()
    )


def consume_and_link(
    *,
    provider: str,
    code: str,
    provider_user_id: str,
    display_name: Optional[str],
    phone: Optional[str],
) -> Dict[str, Any]:
    token = _get_link_token(provider, code)
    if not token:
        return {"ok": False, "reason": "invalid_code"}

    if token.get("used_at"):
        return {"ok": False, "reason": "used_code"}

    expires_at = token.get("expires_at")
    if expires_at:
        try:
            if datetime.fromisoformat(str(expires_at).replace("Z", "+00:00")) <= datetime.now(timezone.utc):
                return {"ok": False, "reason": "expired_code"}
        except Exception:
            pass

    auth_user_id = str(token.get("auth_user_id") or "").strip()
    if not auth_user_id:
        return {"ok": False, "reason": "missing_auth_user"}

    existing = find_account_by_provider_user_id(provider=provider, provider_user_id=provider_user_id)

    if existing and str(existing.get("auth_user_id") or "").strip():
        existing_auth = str(existing.get("auth_user_id") or "").strip()
        if existing_auth != auth_user_id:
            return {"ok": False, "reason": "channel_belongs_to_another_user"}

        _mark_token_used(str(token["id"]), provider_user_id)
        return {
            "ok": True,
            "linked": True,
            "already_linked": True,
            "account_id": existing.get("account_id"),
        }

    if existing and not str(existing.get("auth_user_id") or "").strip():
        claimed = mark_channel_claimed_for_auth_user(
            provider=provider,
            provider_user_id=provider_user_id,
            auth_user_id=auth_user_id,
            display_name=display_name,
            phone=phone,
        )
        if claimed.get("ok"):
            _mark_token_used(str(token["id"]), provider_user_id)
            return {
                "ok": True,
                "linked": True,
                "claimed_existing_channel": True,
                "account_id": claimed.get("account_id"),
            }
        return {"ok": False, "reason": claimed.get("reason") or "claim_failed"}

    linked = upsert_account_link(
        provider=provider,
        provider_user_id=provider_user_id,
        auth_user_id=auth_user_id,
        display_name=display_name,
        phone=phone,
    )
    if not linked.get("ok"):
        return {"ok": False, "reason": linked.get("reason") or "link_failed"}

    _mark_token_used(str(token["id"]), provider_user_id)
    return {
        "ok": True,
        "linked": True,
        "account_id": linked.get("account_id"),
    }
