from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from flask import jsonify, request

from app.core.supabase_client import supabase
from app.services.channel_runtime_guard import check_channel_runtime_access
from app.services.outbound_service import send_whatsapp_text, send_telegram_text

SERVICE_VERSION = "2026-09-07-v1-runtime-http-channel-guard"


def _sb():
    return supabase() if callable(supabase) else supabase


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _identity_account(channel_type: str, provider_user_id: str) -> str:
    try:
        res = (
            _sb().table("channel_identities")
            .select("account_id")
            .eq("channel_type", channel_type)
            .eq("provider_user_id", provider_user_id)
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        return _clean(rows[0].get("account_id")) if rows else ""
    except Exception:
        return ""


def _whatsapp_identity(payload: Dict[str, Any]) -> Tuple[str, str]:
    try:
        entry = (payload.get("entry") or [])[0]
        change = (entry.get("changes") or [])[0]
        value = change.get("value") or {}
        message = (value.get("messages") or [])[0]
        return "whatsapp", _clean(message.get("from"))
    except Exception:
        return "", ""


def _telegram_identity(payload: Dict[str, Any]) -> Tuple[str, str, str]:
    msg = payload.get("message") or payload.get("edited_message") or {}
    user = msg.get("from") or {}
    chat = msg.get("chat") or {}
    return "telegram", _clean(user.get("id")), _clean(chat.get("id"))


def install_channel_runtime_http_guard(app) -> None:
    """Block service consumption from paused/over-limit durable channels.

    This is installed before route dispatch, so every WhatsApp/Telegram command,
    calculator, quiz and AI path is covered without duplicating checks throughout
    the large provider route modules. Provider webhook verification/health GETs
    are unaffected.
    """
    if getattr(app, "_ntg_channel_runtime_http_guard_installed", False):
        return
    app._ntg_channel_runtime_http_guard_installed = True

    @app.before_request
    def _ntg_channel_runtime_http_guard():
        if request.method != "POST":
            return None

        path = request.path.rstrip("/")
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return None

        channel_type = ""
        provider_user_id = ""
        reply_id = ""
        if path in {"/api/webhook", "/api/whatsapp/webhook", "/api/whatsapp/whatsapp/webhook"}:
            channel_type, provider_user_id = _whatsapp_identity(payload)
            reply_id = provider_user_id
        elif path == "/api/telegram/webhook":
            channel_type, provider_user_id, reply_id = _telegram_identity(payload)
        else:
            return None

        # Status callbacks and non-message provider events have no user identity.
        if not channel_type or not provider_user_id:
            return None

        account_id = _identity_account(channel_type, provider_user_id)
        access = check_channel_runtime_access(
            account_id=account_id,
            channel_type=channel_type,
            provider_user_id=provider_user_id,
        )
        if access.get("allowed", True):
            return None

        message = _clean(access.get("message")) or (
            "This connected channel is currently paused. Open the Channels page "
            "on naijataxguides.com to choose an active channel."
        )
        try:
            if channel_type == "whatsapp":
                send_whatsapp_text(reply_id, message)
            elif channel_type == "telegram":
                send_telegram_text(reply_id, message)
        except Exception:
            # Provider retries must not repeatedly consume application services.
            pass

        return jsonify({
            "ok": True,
            "handled": "channel_runtime_blocked",
            "reason": access.get("reason"),
            "selection_required": bool(access.get("selection_required")),
            "paused": bool(access.get("paused")),
            "version": SERVICE_VERSION,
        }), 200
