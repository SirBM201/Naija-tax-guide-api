from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.core.supabase_client import supabase
from app.services.channel_identity_service import (
    create_or_update_channel_identity,
    get_channel_identity,
)

LINK_CODE_RE = re.compile(r"\b([A-Z0-9]{8})\b")
PROVIDER_TO_CHANNEL = {
    "wa": "whatsapp",
    "tg": "telegram",
    "msgr": "messenger",
    "ig": "instagram",
}


def _sb():
    return supabase() if callable(supabase) else supabase


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clip(value: Any, n: int = 240) -> str:
    s = str(value or "")
    return s if len(s) <= n else s[:n] + "…"


def _normalize_provider(provider: str) -> str:
    p = str(provider or "").strip().lower()
    if p in {"wa", "whatsapp", "waba"}:
        return "wa"
    if p in {"tg", "telegram"}:
        return "tg"
    if p in {"msgr", "messenger", "facebook_messenger", "fb_messenger"}:
        return "msgr"
    if p in {"ig", "instagram", "instagram_dm"}:
        return "ig"
    return p


def extract_code(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    m = LINK_CODE_RE.search(str(text).upper())
    return m.group(1) if m else None


def _get_link_token(provider: str, code: str) -> Optional[Dict[str, Any]]:
    res = (
        _sb()
        .table("link_tokens")
        .select("*")
        .eq("provider", provider)
        .eq("code", code)
        .limit(1)
        .execute()
    )
    rows = getattr(res, "data", None) or []
    return rows[0] if rows else None


def _mark_token_used(token_id: str, provider_user_id: str) -> None:
    (
        _sb()
        .table("link_tokens")
        .update(
            {
                "used_at": _now_iso(),
                "used_by_provider_user_id": provider_user_id,
            }
        )
        .eq("id", token_id)
        .execute()
    )


def _safe_iso_to_dt(value: Any) -> Optional[datetime]:
    try:
        if not value:
            return None
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def consume_and_link(
    *,
    provider: str,
    code: str,
    provider_user_id: str,
    display_name: Optional[str],
    phone: Optional[str],
) -> Dict[str, Any]:
    provider = _normalize_provider(provider)
    provider_user_id = str(provider_user_id or "").strip()
    code = str(code or "").strip().upper()

    if provider not in {"wa", "tg", "msgr", "ig"}:
        return {"ok": False, "reason": "invalid_provider"}
    if not provider_user_id:
        return {"ok": False, "reason": "missing_provider_user_id"}
    if not code:
        return {"ok": False, "reason": "missing_code"}

    token = _get_link_token(provider, code)
    if not token:
        return {"ok": False, "reason": "invalid_code"}

    if token.get("used_at"):
        return {"ok": False, "reason": "used_code"}

    expires_at = _safe_iso_to_dt(token.get("expires_at"))
    if expires_at and expires_at <= datetime.now(timezone.utc):
        return {"ok": False, "reason": "expired_code"}

    # IMPORTANT:
    # link_tokens.auth_user_id is being used by this app as the canonical website account_id.
    owner_account_id = str(token.get("auth_user_id") or "").strip()
    if not owner_account_id:
        return {"ok": False, "reason": "missing_account_id_on_token"}

    channel_type = PROVIDER_TO_CHANNEL.get(provider)
    if not channel_type:
        return {"ok": False, "reason": "unsupported_channel_type"}

    try:
        existing_identity = get_channel_identity(
            channel_type=channel_type,
            provider_user_id=provider_user_id,
        )
    except Exception as e:
        return {
            "ok": False,
            "reason": f"identity_lookup_failed:{type(e).__name__}:{_clip(e)}",
        }

    if existing_identity:
        existing_account_id = str(existing_identity.get("account_id") or "").strip()
        if existing_account_id and existing_account_id != owner_account_id:
            return {"ok": False, "reason": "channel_belongs_to_another_user"}

    try:
        linked = create_or_update_channel_identity(
            account_id=owner_account_id,
            channel_type=channel_type,
            provider_user_id=provider_user_id,
            display_name=display_name,
            referral_code=None,
            guest_session_id=None,
        )
    except Exception as e:
        return {
            "ok": False,
            "reason": f"channel_link_failed:{type(e).__name__}:{_clip(e)}",
        }

    if not linked.get("ok"):
        return {
            "ok": False,
            "reason": str(linked.get("error") or linked.get("reason") or "channel_link_failed"),
            "details": linked,
        }

    try:
        _mark_token_used(str(token["id"]), provider_user_id)
    except Exception as e:
        return {
            "ok": False,
            "reason": f"token_mark_used_failed:{type(e).__name__}:{_clip(e)}",
            "details": linked,
        }

    identity = linked.get("channel_identity") or existing_identity or {}
    return {
        "ok": True,
        "linked": True,
        "account_id": owner_account_id,
        "provider": provider,
        "provider_user_id": provider_user_id,
        "channel_type": channel_type,
        "channel_identity": identity,
    }
