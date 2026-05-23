# app/routes/inbound.py
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from flask import Blueprint, jsonify, request

from app.services.accounts_service import upsert_account
from app.services.ask_service import ask_guarded
from app.services.channel_linking_service import consume_and_link, extract_code
from app.services.outbound_service import send_telegram_text, send_whatsapp_text

INBOUND_ROUTE_VERSION = "2026-05-23-v1-clean-safe-telegram-whatsapp"

bp = Blueprint("inbound", __name__)


def _clip(value: Any, limit: int = 700) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit] + "..."


def _json_body() -> Dict[str, Any]:
    return request.get_json(silent=True) or {}


def _extract_account_id_from_upsert(result: Any) -> Optional[str]:
    if not isinstance(result, dict):
        return None

    direct = str(result.get("account_id") or "").strip()
    if direct:
        return direct

    for key in ("account", "row", "data"):
        row = result.get(key)
        if isinstance(row, dict):
            value = str(row.get("account_id") or row.get("id") or "").strip()
            if value:
                return value

    return None


def _provider_for_channel(channel: str) -> str:
    c = str(channel or "").strip().lower()
    if c in {"telegram", "tg"}:
        return "tg"
    return "wa"


def _maybe_link_from_message(
    *,
    channel: str,
    text: str,
    provider_user_id: str,
    display_name: Optional[str] = None,
    phone: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    code = extract_code(text or "")
    if not code:
        return None

    provider = _provider_for_channel(channel)
    try:
        return consume_and_link(
            provider=provider,
            code=code,
            provider_user_id=provider_user_id,
            display_name=display_name,
            phone=phone,
        )
    except Exception as exc:
        return {
            "ok": False,
            "error": "consume_and_link_failed",
            "root_cause": f"{type(exc).__name__}: {_clip(exc)}",
        }


def _extract_whatsapp_text(body: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """
    Extract sender and text from a Meta WhatsApp webhook payload.

    Returns:
        (wa_user_id, text)
    """
    try:
        entry = (body.get("entry") or [None])[0] or {}
        changes = (entry.get("changes") or [None])[0] or {}
        value = changes.get("value") or {}

        messages = value.get("messages") or []
        if not messages:
            return None, None

        msg = messages[0] or {}
        wa_user_id = str(msg.get("from") or "").strip()
        if not wa_user_id:
            return None, None

        msg_type = str(msg.get("type") or "").strip().lower()
        if msg_type != "text":
            return wa_user_id, None

        text = str((msg.get("text") or {}).get("body") or "").strip()
        return wa_user_id, text or None
    except Exception:
        return None, None


def _extract_telegram_text(body: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """
    Extract chat ID, user ID, display name, and text from Telegram update payload.

    Returns:
        (tg_chat_id, tg_user_id, display_name, text)
    """
    msg = body.get("message") or body.get("edited_message") or {}

    if not msg and body.get("callback_query"):
        callback = body.get("callback_query") or {}
        msg = callback.get("message") or {}
        if not msg:
            msg = {"from": callback.get("from") or {}, "text": callback.get("data") or ""}

    if not msg:
        return None, None, None, None

    chat = msg.get("chat") or {}
    tg_chat_id = str(chat.get("id") or "").strip()

    sender = msg.get("from") or {}
    tg_user_id = str(sender.get("id") or "").strip()

    first_name = str(sender.get("first_name") or "").strip()
    last_name = str(sender.get("last_name") or "").strip()
    username = str(sender.get("username") or "").strip()
    display_name = " ".join([p for p in [first_name, last_name] if p]).strip() or username or None

    text = str(msg.get("text") or "").strip()

    if not tg_chat_id or not tg_user_id:
        return tg_chat_id or None, tg_user_id or None, display_name, None

    return tg_chat_id, tg_user_id, display_name, text or None


def _ask_for_channel(*, account_id: str, question: str, channel: str, lang: str = "en") -> Dict[str, Any]:
    """
    Compatibility wrapper for the current ask_service.ask_guarded signature.

    Current main ask_guarded expects one body dict. Older inbound code passed
    keyword arguments, which would fail if this route is registered later.
    """
    return ask_guarded(
        {
            "account_id": account_id,
            "question": question,
            "lang": lang,
            "channel": channel,
        }
    )


def _extract_answer(resp: Any) -> str:
    if not isinstance(resp, dict):
        return ""
    return str(resp.get("answer") or resp.get("message") or "").strip()


@bp.get("/inbound/health")
def inbound_health():
    return jsonify(
        {
            "ok": True,
            "service": "inbound",
            "version": INBOUND_ROUTE_VERSION,
        }
    ), 200


@bp.post("/inbound/whatsapp")
def whatsapp_inbound():
    body = _json_body()
    wa_user_id, text = _extract_whatsapp_text(body)

    if not wa_user_id:
        return jsonify({"ok": True, "ignored": True, "reason": "no_sender_or_status"}), 200

    if not text:
        return jsonify({"ok": True, "ignored": True, "reason": "no_text"}), 200

    upsert_result = upsert_account(
        provider="wa",
        provider_user_id=wa_user_id,
        display_name=None,
        phone=wa_user_id,
    )
    account_id = _extract_account_id_from_upsert(upsert_result)

    if not account_id:
        return jsonify(
            {
                "ok": False,
                "error": "account_upsert_failed",
                "root_cause": _clip(
                    (upsert_result or {}).get("root_cause")
                    or (upsert_result or {}).get("error")
                    or "upsert_account returned no account_id"
                ),
                "fix": (upsert_result or {}).get("fix")
                or "Fix accounts_service.upsert_account to always return accounts.account_id.",
                "details": {"provider": "wa", "provider_user_id": wa_user_id},
            }
        ), 500

    link_result = _maybe_link_from_message(
        channel="whatsapp",
        text=text,
        provider_user_id=wa_user_id,
        display_name=None,
        phone=wa_user_id,
    )
    if link_result and link_result.get("ok"):
        send_whatsapp_text(wa_user_id, "Linked successfully. You can now use the service.")
        return jsonify({"ok": True, "linked": True, "link": link_result}), 200

    response = _ask_for_channel(
        account_id=account_id,
        question=text,
        lang="en",
        channel="whatsapp",
    )

    answer = _extract_answer(response)
    if answer:
        send_whatsapp_text(wa_user_id, answer)

    return jsonify(response), 200


@bp.post("/inbound/telegram")
def telegram_inbound():
    body = _json_body()
    tg_chat_id, tg_user_id, display_name, text = _extract_telegram_text(body)

    if not tg_chat_id or not tg_user_id:
        return jsonify({"ok": True, "ignored": True, "reason": "no_sender"}), 200

    if not text:
        return jsonify({"ok": True, "ignored": True, "reason": "no_text"}), 200

    upsert_result = upsert_account(
        provider="tg",
        provider_user_id=tg_user_id,
        display_name=display_name,
        phone=None,
    )
    account_id = _extract_account_id_from_upsert(upsert_result)

    if not account_id:
        return jsonify(
            {
                "ok": False,
                "error": "account_upsert_failed",
                "root_cause": _clip(
                    (upsert_result or {}).get("root_cause")
                    or (upsert_result or {}).get("error")
                    or "upsert_account returned no account_id"
                ),
                "fix": (upsert_result or {}).get("fix")
                or "Fix accounts_service.upsert_account to always return accounts.account_id.",
                "details": {"provider": "tg", "provider_user_id": tg_user_id},
            }
        ), 500

    link_result = _maybe_link_from_message(
        channel="telegram",
        text=text,
        provider_user_id=tg_user_id,
        display_name=display_name,
        phone=None,
    )
    if link_result and link_result.get("ok"):
        send_telegram_text(tg_chat_id, "Linked successfully. You can now use the service.")
        return jsonify({"ok": True, "linked": True, "link": link_result}), 200

    response = _ask_for_channel(
        account_id=account_id,
        question=text,
        lang="en",
        channel="telegram",
    )

    answer = _extract_answer(response)
    if answer:
        send_telegram_text(tg_chat_id, answer)

    return jsonify(response), 200
