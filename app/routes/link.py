# app/routes/link.py
from __future__ import annotations

import hashlib
import logging
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple
from urllib.parse import quote

from flask import Blueprint, jsonify as _flask_jsonify, request

from app.core.supabase_client import supabase
from app.services.auth_service import get_current_user
from app.services.channel_identity_runtime_service import get_channel_identity_by_account

try:
    from app.services.web_auth_service import get_account_id_from_request
except Exception:  # pragma: no cover
    get_account_id_from_request = None  # type: ignore



try:
    from app.core.response_safety import sanitize_response_payload
except Exception:  # pragma: no cover
    def sanitize_response_payload(payload, request_obj=None):
        return payload


def jsonify(*args, **kwargs):
    """Local safe jsonify wrapper that strips debug/internal payload keys in production."""
    if len(args) == 1 and isinstance(args[0], (dict, list)) and not kwargs:
        return _flask_jsonify(sanitize_response_payload(args[0], request))
    return _flask_jsonify(*args, **kwargs)


logger = logging.getLogger(__name__)

# app/__init__.py registers this blueprint with /api.
bp = Blueprint("link", __name__)

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


def _json_error(message: str, status: int = 400, **extra: Any):
    payload: Dict[str, Any] = {"ok": False, "error": message}
    if extra:
        payload.update(extra)
    return jsonify(payload), status


def _normalize_provider(raw: Any) -> str:
    v = _clean(raw).lower()
    if v in {"wa", "whatsapp", "waba"}:
        return "wa"
    if v in {"tg", "telegram"}:
        return "tg"
    return v


def _channel_type(provider: Any) -> Optional[str]:
    provider = _normalize_provider(provider)
    if provider == "wa":
        return "whatsapp"
    if provider == "tg":
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
    debug: Dict[str, Any] = {
        "resolver": "link_generate_alias_safe_v4",
        "flask_session_checked": True,
        "flask_session_user_found": False,
        "web_token_checked": False,
    }

    try:
        user = get_current_user()
    except Exception as exc:
        user = None
        debug["flask_session_error"] = f"{type(exc).__name__}: {exc}"

    if user:
        debug["flask_session_user_found"] = True
        debug["flask_session_user_keys"] = sorted(list(user.keys()))
        account_id = _clean(user.get("account_id")) or _clean(user.get("id"))
        if account_id:
            debug["account_source"] = "flask_session"
            return account_id, debug

    if get_account_id_from_request is not None:
        try:
            debug["web_token_checked"] = True
            account_id, token_debug = get_account_id_from_request(request)  # type: ignore[misc]
            account_id = _clean(account_id)
            debug["web_token_debug"] = token_debug
            if account_id:
                debug["account_source"] = "web_token"
                return account_id, debug
        except Exception as exc:
            debug["web_token_error"] = f"{type(exc).__name__}: {exc}"

    debug["root_cause"] = "No logged-in account was resolved from ntg_session or web token."
    return None, debug


def _identity_payload(identity: Optional[Dict[str, Any]], channel_type: str) -> Dict[str, Any]:
    if not identity:
        return {
            "linked": False,
            "is_verified": False,
            "verified": False,
            "provider_user_id": None,
            "display_name": None,
            "value": None,
            "phone": None,
            "username": None,
            "updated_at": None,
            "last_seen_at": None,
        }

    metadata = identity.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}

    provider_user_id = _clean(identity.get("provider_user_id")) or None
    display_name = _clean(metadata.get("display_name") or identity.get("display_name")) or None
    verified = bool(identity.get("is_verified") or identity.get("verified"))
    updated_at = identity.get("last_seen_at") or identity.get("updated_at") or identity.get("created_at")

    return {
        "linked": True,
        "is_verified": verified,
        "verified": verified,
        "provider_user_id": provider_user_id,
        "display_name": display_name,
        "value": display_name or provider_user_id,
        "phone": provider_user_id if channel_type == "whatsapp" else None,
        "username": (display_name or provider_user_id) if channel_type == "telegram" else None,
        "updated_at": updated_at,
        "last_seen_at": identity.get("last_seen_at"),
        "raw": identity,
    }


def _safe_insert_link_token(payloads: list[Dict[str, Any]]):
    last_error: Optional[Exception] = None
    for payload in payloads:
        try:
            return _sb().table("link_tokens").insert(payload).execute()
        except Exception as exc:
            last_error = exc
            logger.warning("Link token insert fallback after error: %r", exc)
            continue
    if last_error:
        raise last_error
    raise RuntimeError("No link token payload supplied")


def _expire_previous_tokens(account_id: str, provider: str) -> None:
    """
    Mark previous unused link tokens for this account/provider as used/expired.
    Non-fatal because older databases may use either used_at or used.
    """
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
        logger.info("Previous link token used_at expiry skipped: %r", exc)

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
        logger.info("Previous link token used expiry skipped: %r", exc)


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
    """
    Unlink only the current user's selected channel.
    This intentionally avoids deleting all link_tokens for a provider globally.
    """
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


@bp.get("/link/health")
def link_health():
    return jsonify({"ok": True, "service": "link", "version": "generate_alias_safe_v4"}), 200


@bp.get("/link/status")
def get_link_status():
    account_id, debug = _resolve_account_id()
    if not account_id:
        logger.warning("Link status unauthorized: %s", debug)
        return jsonify({"ok": False, "error": "unauthorized", "debug": debug}), 401

    wa_identity = None
    tg_identity = None
    errors: Dict[str, str] = {}

    try:
        wa_identity = get_channel_identity_by_account(account_id=account_id, channel_type="whatsapp")
    except Exception as exc:
        errors["whatsapp"] = f"{type(exc).__name__}: {exc}"

    try:
        tg_identity = get_channel_identity_by_account(account_id=account_id, channel_type="telegram")
    except Exception as exc:
        errors["telegram"] = f"{type(exc).__name__}: {exc}"

    whatsapp = _identity_payload(wa_identity, "whatsapp")
    telegram = _identity_payload(tg_identity, "telegram")

    return jsonify(
        {
            "ok": True,
            "account_id": account_id,
            "whatsapp": whatsapp,
            "telegram": telegram,
            "whatsapp_linked": bool(whatsapp.get("linked")),
            "telegram_linked": bool(telegram.get("linked")),
            "whatsapp_verified": bool(whatsapp.get("is_verified")),
            "telegram_verified": bool(telegram.get("is_verified")),
            "whatsapp_number": whatsapp.get("phone") or whatsapp.get("provider_user_id"),
            "telegram_username": telegram.get("username") or telegram.get("provider_user_id"),
            "whatsapp_updated_at": whatsapp.get("updated_at"),
            "telegram_updated_at": telegram.get("updated_at"),
            "debug": debug,
            "non_fatal_errors": errors,
        }
    ), 200


# Frontend compatibility:
# - POST /api/link/generate?provider=wa|tg
# - GET  /api/link/generate?provider=wa|tg
# - POST /api/link/generate-code?provider=wa|tg
@bp.route("/link/generate", methods=["POST", "GET"])
@bp.route("/link/generate-code", methods=["POST", "GET"])
def generate_link_code():
    account_id, debug = _resolve_account_id()
    if not account_id:
        return _json_error("Unauthorized", 401, debug=debug)

    provider = _requested_provider()
    if provider == "__mismatch__":
        return _json_error("Provider mismatch between query and body", 400)
    if provider not in {"wa", "tg"}:
        return _json_error("Invalid provider. Use wa or tg.", 400)

    code = _generate_code()
    expires_at = _utcnow() + timedelta(minutes=TOKEN_EXPIRY_MINUTES)

    try:
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
        logger.exception("Generate link code failed")
        return _json_error(
            "Failed to generate link code",
            500,
            root_cause=f"{type(exc).__name__}: {exc}",
            account_id=account_id,
            provider=provider,
            debug=debug,
        )


@bp.route("/link/unlink", methods=["POST", "DELETE"])
def unlink_linked_channel():
    account_id, debug = _resolve_account_id()
    if not account_id:
        return _json_error("Unauthorized", 401, debug=debug)

    provider = _requested_provider()
    if provider == "__mismatch__":
        return _json_error("Provider mismatch between query and body", 400)
    if provider not in {"wa", "tg"}:
        return _json_error("Invalid provider. Use wa or tg.", 400)

    channel_type = _channel_type(provider)
    if not channel_type:
        return _json_error("Invalid provider. Use wa or tg.", 400)

    result = _safe_unlink_identity(account_id, channel_type, provider)
    if not result.get("ok"):
        return _json_error("Failed to unlink channel", 400, details=result)

    return jsonify(result), 200
