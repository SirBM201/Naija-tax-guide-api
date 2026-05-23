# app/routes/channel.py
from __future__ import annotations

import hashlib
import logging
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple
from urllib.parse import quote

from flask import Blueprint, jsonify, request

from app.core.supabase_client import supabase
from app.services.auth_service import get_current_user
from app.services.channel_identity_runtime_service import get_channel_identity_by_account

logger = logging.getLogger(__name__)

# Do NOT add url_prefix here. app/__init__.py registers this blueprint with /api.
bp = Blueprint("channel", __name__)

TOKEN_LENGTH = int(os.getenv("LINK_TOKEN_LENGTH", "8") or "8")
TOKEN_EXPIRY_MINUTES = int(os.getenv("LINK_TOKEN_EXPIRY_MINUTES", "30") or "30")
SAFE_CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"

WHATSAPP_TEST_LINE_E164 = (os.getenv("WHATSAPP_TEST_LINE_E164") or "").strip()
WHATSAPP_DEEP_LINK = (os.getenv("WHATSAPP_DEEP_LINK") or "").strip()
TELEGRAM_BOT_USERNAME = (os.getenv("TELEGRAM_BOT_USERNAME") or "").strip().lstrip("@")
TELEGRAM_BOT_URL = (os.getenv("TELEGRAM_BOT_URL") or "").strip()


def _sb():
    return supabase() if callable(supabase) else supabase


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _utcnow().isoformat()


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize_provider(provider: Any) -> str:
    p = _clean(provider).lower()
    if p in {"wa", "whatsapp", "waba"}:
        return "wa"
    if p in {"tg", "telegram"}:
        return "tg"
    return p


def _channel_type(provider: Any) -> Optional[str]:
    p = _normalize_provider(provider)
    if p == "wa":
        return "whatsapp"
    if p == "tg":
        return "telegram"
    return None


def _requested_provider() -> str:
    body = request.get_json(silent=True) or {}
    provider_from_query = _normalize_provider(request.args.get("provider") or "")
    provider_from_body = _normalize_provider(body.get("provider") or "")

    if provider_from_query and provider_from_body and provider_from_query != provider_from_body:
        return "__mismatch__"

    return provider_from_query or provider_from_body


def _generate_code(length: int = TOKEN_LENGTH) -> str:
    length = max(6, min(int(length or TOKEN_LENGTH), 12))
    return "".join(secrets.choice(SAFE_CODE_ALPHABET) for _ in range(length))


def _build_whatsapp_link(code: str) -> str:
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
        return f"https://t.me/{TELEGRAM_BOT_USERNAME}?start={message}"

    return None


def _resolve_account_id() -> Tuple[Optional[str], Dict[str, Any]]:
    debug: Dict[str, Any] = {"resolver": "channel_v4", "flask_session_user_found": False}
    try:
        user = get_current_user()
    except Exception as exc:
        user = None
        debug["flask_session_error"] = f"{type(exc).__name__}: {exc}"

    if not user:
        debug["root_cause"] = "No valid ntg_session user found."
        return None, debug

    debug["flask_session_user_found"] = True
    debug["flask_session_user_keys"] = sorted(list(user.keys()))
    account_id = _clean(user.get("account_id")) or _clean(user.get("id"))
    if not account_id:
        debug["root_cause"] = "Logged-in session exists but account_id/id is missing."
        return None, debug
    return account_id, debug


def _identity_payload(identity: Optional[Dict[str, Any]], channel_type: str) -> Dict[str, Any]:
    if not identity:
        return {"provider": channel_type, "linked": False, "is_verified": False, "verified": False}

    metadata = identity.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}

    provider_user_id = _clean(identity.get("provider_user_id")) or None
    display_name = _clean(metadata.get("display_name") or identity.get("display_name")) or None
    verified = bool(identity.get("is_verified") or identity.get("verified"))

    return {
        "provider": channel_type,
        "linked": True,
        "is_verified": verified,
        "verified": verified,
        "provider_user_id": provider_user_id,
        "display_name": display_name,
        "value": display_name or provider_user_id,
        "phone": provider_user_id if channel_type == "whatsapp" else None,
        "username": (display_name or provider_user_id) if channel_type == "telegram" else None,
        "linked_at": identity.get("created_at"),
        "updated_at": identity.get("last_seen_at") or identity.get("updated_at") or identity.get("created_at"),
        "raw": identity,
    }


def _status_for_account(account_id: str) -> Tuple[Dict[str, Any], int]:
    errors: Dict[str, str] = {}
    wa = None
    tg = None

    try:
        wa = get_channel_identity_by_account(account_id=account_id, channel_type="whatsapp")
    except Exception as exc:
        errors["whatsapp"] = f"{type(exc).__name__}: {exc}"

    try:
        tg = get_channel_identity_by_account(account_id=account_id, channel_type="telegram")
    except Exception as exc:
        errors["telegram"] = f"{type(exc).__name__}: {exc}"

    whatsapp = _identity_payload(wa, "whatsapp")
    telegram = _identity_payload(tg, "telegram")
    channels = [c for c in [whatsapp, telegram] if c.get("linked")]
    linked = []
    if whatsapp.get("linked"):
        linked.append("whatsapp")
    if telegram.get("linked"):
        linked.append("telegram")

    return {
        "ok": True,
        "account_id": account_id,
        "channels": channels,
        "linked": linked,
        "available": ["whatsapp", "telegram"],
        "whatsapp": whatsapp,
        "telegram": telegram,
        "whatsapp_linked": bool(whatsapp.get("linked")),
        "telegram_linked": bool(telegram.get("linked")),
        "whatsapp_verified": bool(whatsapp.get("is_verified")),
        "telegram_verified": bool(telegram.get("is_verified")),
        "non_fatal_errors": errors,
    }, 200


def _safe_insert_link_token(payloads: list[Dict[str, Any]]):
    last_error: Optional[Exception] = None
    for payload in payloads:
        try:
            return _sb().table("link_tokens").insert(payload).execute()
        except Exception as exc:
            last_error = exc
            logger.warning("Channel token insert fallback after error: %r", exc)
            continue
    if last_error:
        raise last_error
    raise RuntimeError("No link token payload supplied")


def _expire_previous_tokens(account_id: str, provider: str) -> None:
    now_iso = _now_iso()
    try:
        (
            _sb()
            .table("link_tokens")
            .update({"used_at": now_iso})
            .eq("auth_user_id", account_id)
            .eq("provider", provider)
            .is_("used_at", "null")
            .execute()
        )
    except Exception as exc:
        logger.info("Previous channel token used_at expiry skipped: %r", exc)

    try:
        (
            _sb()
            .table("link_tokens")
            .update({"used": True})
            .eq("auth_user_id", account_id)
            .eq("provider", provider)
            .eq("used", False)
            .execute()
        )
    except Exception as exc:
        logger.info("Previous channel token used expiry skipped: %r", exc)


def _create_link_token(account_id: str, provider: str, code: str, expires_at: datetime) -> None:
    now = _utcnow()
    code_hash = _sha256_hex(code)
    base_payload = {
        "id": str(uuid.uuid4()),
        "auth_user_id": account_id,
        "provider": provider,
        "code": code,
        "code_hash": code_hash,
        "expires_at": expires_at.isoformat(),
        "used_at": None,
        "used": False,
        "provider_user_id": None,
        "created_at": now.isoformat(),
    }

    _safe_insert_link_token(
        [
            base_payload,
            {k: v for k, v in base_payload.items() if k not in {"used"}},
            {k: v for k, v in base_payload.items() if k not in {"code_hash", "used"}},
            {k: v for k, v in base_payload.items() if k not in {"id", "code_hash", "used", "provider_user_id"}},
            {
                "auth_user_id": account_id,
                "provider": provider,
                "code": code,
                "expires_at": expires_at.isoformat(),
                "created_at": now.isoformat(),
                "used": False,
            },
        ]
    )


def _safe_unlink_identity(account_id: str, channel_type: str, provider: str) -> Dict[str, Any]:
    try:
        identity = get_channel_identity_by_account(account_id=account_id, channel_type=channel_type)
    except Exception as exc:
        return {
            "ok": False,
            "error": "identity_lookup_failed",
            "root_cause": f"{type(exc).__name__}: {exc}",
        }

    if not identity:
        return {"ok": True, "unlinked": False, "reason": "not_linked", "provider": provider}

    provider_user_id = _clean(identity.get("provider_user_id"))

    try:
        (
            _sb()
            .table("channel_identities")
            .delete()
            .eq("account_id", account_id)
            .eq("channel_type", channel_type)
            .execute()
        )
    except Exception as exc:
        return {
            "ok": False,
            "error": "identity_delete_failed",
            "root_cause": f"{type(exc).__name__}: {exc}",
        }

    _expire_previous_tokens(account_id, provider)

    return {
        "ok": True,
        "unlinked": True,
        "provider": provider,
        "channel_type": channel_type,
        "provider_user_id": provider_user_id,
    }


@bp.get("/channel/health")
def channel_health():
    return jsonify({"ok": True, "service": "channel", "version": "safe_client_v4"}), 200


@bp.get("/channel/status")
def channel_status():
    account_id, debug = _resolve_account_id()
    if not account_id:
        return jsonify({"ok": False, "error": "unauthorized", "debug": debug}), 401

    payload, code = _status_for_account(account_id)
    payload["debug"] = debug
    return jsonify(payload), code


@bp.post("/channel/generate-code")
def generate_code():
    account_id, debug = _resolve_account_id()
    if not account_id:
        return jsonify({"ok": False, "error": "unauthorized", "debug": debug}), 401

    provider = _requested_provider()
    if provider == "__mismatch__":
        return jsonify({"ok": False, "error": "Provider mismatch between query and body"}), 400
    if provider not in {"wa", "tg"}:
        return jsonify({"ok": False, "error": "Invalid provider. Use wa or tg."}), 400

    try:
        code = _generate_code()
        expires_at = _utcnow() + timedelta(minutes=TOKEN_EXPIRY_MINUTES)

        _expire_previous_tokens(account_id, provider)
        _create_link_token(account_id, provider, code, expires_at)

        whatsapp_url = _build_whatsapp_link(code) if provider == "wa" else None
        telegram_url = _build_telegram_link(code) if provider == "tg" else None
        deep_link = whatsapp_url if provider == "wa" else telegram_url

        return jsonify(
            {
                "ok": True,
                "account_id": account_id,
                "provider": provider,
                "channel_type": _channel_type(provider),
                "code": code,
                "expires_in_minutes": TOKEN_EXPIRY_MINUTES,
                "expires_at": expires_at.isoformat(),
                "deep_link": deep_link,
                "link_url": deep_link,
                "whatsapp_url": whatsapp_url,
                "telegram_url": telegram_url,
                "bot_url": telegram_url,
                "message": f"Send this code to the {'WhatsApp' if provider == 'wa' else 'Telegram'} bot to link your account.",
                "debug": debug,
            }
        ), 200
    except Exception as exc:
        logger.exception("Generate channel code error")
        return jsonify(
            {
                "ok": False,
                "error": "generate_code_failed",
                "root_cause": f"{type(exc).__name__}: {exc}",
                "debug": debug,
            }
        ), 500


@bp.delete("/channel/unlink")
@bp.post("/channel/unlink")
def unlink():
    account_id, debug = _resolve_account_id()
    if not account_id:
        return jsonify({"ok": False, "error": "unauthorized", "debug": debug}), 401

    provider = _requested_provider()
    if provider == "__mismatch__":
        return jsonify({"ok": False, "error": "Provider mismatch between query and body"}), 400
    if provider not in {"wa", "tg"}:
        return jsonify({"ok": False, "error": "Invalid provider. Use wa or tg."}), 400

    channel_type = _channel_type(provider)
    if not channel_type:
        return jsonify({"ok": False, "error": "Invalid provider. Use wa or tg."}), 400

    result = _safe_unlink_identity(account_id, channel_type, provider)
    if not result.get("ok"):
        return jsonify({"ok": False, "error": "unlink_failed", "details": result}), 400

    return jsonify(result), 200


@bp.get("/channel/linked")
def linked_channels():
    account_id, debug = _resolve_account_id()
    if not account_id:
        return jsonify({"ok": False, "error": "unauthorized", "debug": debug}), 401

    payload, code = _status_for_account(account_id)
    payload["debug"] = debug
    return jsonify(payload), code
