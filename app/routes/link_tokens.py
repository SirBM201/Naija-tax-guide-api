from __future__ import annotations

import hashlib
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple
from urllib.parse import quote

from flask import Blueprint, jsonify, request
from supabase import create_client

from app.services.channel_identity_runtime_service import get_channel_identity_by_account
from app.services.channel_linking_service import consume_and_link, unlink_channel
from app.services.web_auth_service import get_account_id_from_request

SUPABASE_URL = (os.getenv("SUPABASE_URL") or "").strip()
SUPABASE_SERVICE_ROLE_KEY = (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip()

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    raise RuntimeError("SUPABASE env vars missing")

sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

bp = Blueprint("link_tokens", __name__, url_prefix="/link")

TOKEN_LENGTH = 8
TOKEN_EXPIRY_MINUTES = 30
SAFE_CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"

WHATSAPP_TEST_LINE_E164 = (os.getenv("WHATSAPP_TEST_LINE_E164") or "").strip()
WHATSAPP_DEEP_LINK = (os.getenv("WHATSAPP_DEEP_LINK") or "").strip()
TELEGRAM_BOT_USERNAME = (os.getenv("TELEGRAM_BOT_USERNAME") or "").strip().lstrip("@")
TELEGRAM_BOT_URL = (os.getenv("TELEGRAM_BOT_URL") or "").strip()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json_error(message: str, status: int = 400, **extra: Any):
    payload: Dict[str, Any] = {"ok": False, "error": message}
    if extra:
        payload.update(extra)
    return jsonify(payload), status


def _normalize_provider(raw: str) -> str:
    v = (raw or "").strip().lower()
    if v in {"tg", "telegram"}:
        return "tg"
    if v in {"wa", "whatsapp"}:
        return "wa"
    if v in {"msgr", "messenger"}:
        return "msgr"
    if v in {"ig", "instagram"}:
        return "ig"
    return v


def _generate_code(length: int = TOKEN_LENGTH) -> str:
    return "".join(secrets.choice(SAFE_CODE_ALPHABET) for _ in range(length))


def _get_logged_in_account_id() -> Tuple[Optional[str], Dict[str, Any]]:
    try:
        account_id, dbg = get_account_id_from_request(request)
        account_id = (account_id or "").strip() or None
        return account_id, dbg if isinstance(dbg, dict) else {}
    except Exception as e:
        return None, {"ok": False, "error": "auth_resolution_failed", "detail": repr(e)}


def _build_whatsapp_link(code: str) -> Optional[str]:
    message = quote(code)

    if WHATSAPP_DEEP_LINK:
        separator = "&" if "?" in WHATSAPP_DEEP_LINK else "?"
        if "text=" in WHATSAPP_DEEP_LINK:
            return WHATSAPP_DEEP_LINK
        return f"{WHATSAPP_DEEP_LINK}{separator}text={message}"

    phone = "".join(ch for ch in WHATSAPP_TEST_LINE_E164 if ch.isdigit())
    if phone:
        return f"https://wa.me/{phone}?text={message}"

    return f"https://wa.me/?text={message}"


def _build_telegram_link(code: str) -> Optional[str]:
    message = quote(code)

    if TELEGRAM_BOT_URL:
        separator = "&" if "?" in TELEGRAM_BOT_URL else "?"
        if "start=" in TELEGRAM_BOT_URL or "text=" in TELEGRAM_BOT_URL:
            return TELEGRAM_BOT_URL
        return f"{TELEGRAM_BOT_URL}{separator}text={message}"

    if TELEGRAM_BOT_USERNAME:
        return f"https://t.me/{TELEGRAM_BOT_USERNAME}?text={message}"

    return None


def _find_channel_identity_for_account(account_id: str, provider: str) -> Optional[Dict[str, Any]]:
    channel_type = "telegram" if provider == "tg" else "whatsapp" if provider == "wa" else None
    if not channel_type:
        return None
    try:
        return get_channel_identity_by_account(account_id=account_id, channel_type=channel_type)
    except Exception:
        return None


@bp.get("/status")
def link_status():
    account_id, auth_dbg = _get_logged_in_account_id()
    if not account_id:
        return _json_error("Unauthorized", 401, auth=auth_dbg)

    tg_identity = _find_channel_identity_for_account(account_id, "tg")
    wa_identity = _find_channel_identity_for_account(account_id, "wa")

    return jsonify(
        {
            "ok": True,
            "account_id": account_id,
            "telegram": {
                "linked": bool(tg_identity),
                "provider_user_id": (tg_identity or {}).get("provider_user_id") if tg_identity else None,
                "display_name": ((tg_identity or {}).get("metadata") or {}).get("display_name") if tg_identity else None,
                "updated_at": (tg_identity or {}).get("last_seen_at") if tg_identity else None,
                "is_verified": bool((tg_identity or {}).get("is_verified")),
            },
            "whatsapp": {
                "linked": bool(wa_identity),
                "provider_user_id": (wa_identity or {}).get("provider_user_id") if wa_identity else None,
                "display_name": ((wa_identity or {}).get("metadata") or {}).get("display_name") if wa_identity else None,
                "updated_at": (wa_identity or {}).get("last_seen_at") if wa_identity else None,
                "is_verified": bool((wa_identity or {}).get("is_verified")),
            },
        }
    )


@bp.post("/generate")
def generate_link_code():
    account_id, auth_dbg = _get_logged_in_account_id()
    if not account_id:
        return _json_error("Unauthorized", 401, auth=auth_dbg)

    body = request.get_json(silent=True) or {}
    provider = _normalize_provider(body.get("provider") or "")

    if provider not in {"wa", "tg"}:
        return _json_error("Invalid provider", 400)

    code = _generate_code()
    code_hash = _sha256_hex(code)
    now = _utcnow()
    expires_at = now + timedelta(minutes=TOKEN_EXPIRY_MINUTES)

    try:
        (
            sb.table("link_tokens")
            .update({"used_at": now.isoformat()})
            .eq("auth_user_id", account_id)
            .eq("provider", provider)
            .is_("used_at", "null")
            .execute()
        )
    except Exception:
        pass

    insert_payload = {
        "id": str(uuid.uuid4()),
        # Historical column name, but the app stores canonical website account_id here.
        "auth_user_id": account_id,
        "provider": provider,
        "code": code,
        "code_hash": code_hash,
        "expires_at": expires_at.isoformat(),
        "used_at": None,
        "provider_user_id": None,
        "created_at": now.isoformat(),
    }

    try:
        sb.table("link_tokens").insert(insert_payload).execute()
    except Exception as e:
        return _json_error(
            "Failed to generate link code",
            500,
            detail=repr(e),
            account_id=account_id,
            provider=provider,
        )

    whatsapp_url = _build_whatsapp_link(code) if provider == "wa" else None
    telegram_url = _build_telegram_link(code) if provider == "tg" else None
    deep_link = whatsapp_url if provider == "wa" else telegram_url

    return jsonify(
        {
            "ok": True,
            "account_id": account_id,
            "provider": provider,
            "code": code,
            "expires_in_minutes": TOKEN_EXPIRY_MINUTES,
            "expires_at": expires_at.isoformat(),
            "deep_link": deep_link,
            "link_url": deep_link,
            "whatsapp_url": whatsapp_url,
            "telegram_url": telegram_url,
            "bot_url": telegram_url,
        }
    )




@bp.post("/unlink")
def unlink_linked_channel():
    account_id, auth_dbg = _get_logged_in_account_id()
    if not account_id:
        return _json_error("Unauthorized", 401, auth=auth_dbg)

    body = request.get_json(silent=True) or {}
    provider = _normalize_provider(body.get("provider") or "")
    if provider not in {"wa", "tg"}:
        return _json_error("Invalid provider", 400)

    identity = _find_channel_identity_for_account(account_id, provider)
    provider_user_id = str((identity or {}).get("provider_user_id") or "").strip()
    if not provider_user_id:
        return jsonify({"ok": True, "unlinked": False, "reason": "not_linked"})

    result = unlink_channel(provider=provider, provider_user_id=provider_user_id)
    if not result.get("ok"):
        return _json_error("Failed to unlink channel", 400, details=result)
    return jsonify({"ok": True, "unlinked": True, "provider": provider, "provider_user_id": provider_user_id})

@bp.post("/consume")
def consume_link_code():
    body = request.get_json(silent=True) or {}

    code = (body.get("code") or "").strip().upper()
    provider = _normalize_provider(body.get("provider") or "")
    provider_user_id = str(body.get("provider_user_id") or "").strip()
    display_name = (body.get("display_name") or "").strip() or None
    phone = (body.get("phone") or "").strip() or None
    phone_e164 = (body.get("phone_e164") or "").strip() or None

    if not code or provider not in {"wa", "tg", "msgr", "ig"} or not provider_user_id:
        return _json_error("Invalid request", 400)

    link = consume_and_link(
        provider=provider,
        code=code,
        provider_user_id=provider_user_id,
        display_name=display_name,
        phone=phone_e164 or phone,
    )

    if not link.get("ok"):
        reason = str(link.get("reason") or link.get("error") or "link_failed").strip()
        if reason in {"invalid_code", "expired_code", "used_code"}:
            return _json_error("Invalid or expired code", 404, details=link)
        return _json_error("Failed to link account", 400, details=link)

    return jsonify(
        {
            "ok": True,
            "linked": True,
            "account_id": link.get("account_id"),
            "provider": provider,
            "provider_user_id": provider_user_id,
            "link": link,
        }
    )
