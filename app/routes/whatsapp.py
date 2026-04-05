from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
from typing import Any, Dict, Optional, Tuple

from flask import Blueprint, jsonify, request

from app.services.accounts_service import lookup_account, upsert_account
from app.services.ask_service import ask_guarded
from app.services.channel_identity_service import get_channel_identity
from app.services.channel_linking_service import consume_and_link
from app.services.outbound_service import send_whatsapp_text

bp = Blueprint("whatsapp", __name__)

WA_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "").strip()
WA_APP_SECRET = os.getenv("WHATSAPP_APP_SECRET", "").strip()
LINK_CODE_RE = re.compile(r"^[A-Z0-9]{8}$")


def _clip(value: Any, n: int = 220) -> str:
    s = str(value or "")
    return s if len(s) <= n else s[:n] + "…"


def _verify_meta_signature(raw_body: bytes) -> bool:
    if not WA_APP_SECRET:
        return True

    signature = (request.headers.get("X-Hub-Signature-256") or "").strip()
    if not signature.startswith("sha256="):
        return False

    expected = "sha256=" + hmac.new(
        WA_APP_SECRET.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(signature, expected)


def _extract_message(body: Dict[str, Any]) -> Tuple[str, str]:
    entry = (body.get("entry") or [None])[0] or {}
    changes = (entry.get("changes") or [None])[0] or {}
    value = changes.get("value") or {}
    messages = value.get("messages") or []
    if not messages:
        return "", ""

    msg = messages[0]
    from_phone = str(msg.get("from") or "").strip()
    msg_type = msg.get("type")
    text = ""
    if msg_type == "text":
        text = str((msg.get("text") or {}).get("body") or "").strip()
    return from_phone, text


def _is_link_code(text: str) -> bool:
    return bool(LINK_CODE_RE.match(str(text or "").strip().upper()))


def _resolve_linked_account_id(from_phone: str) -> str:
    try:
        identity = get_channel_identity(channel_type="whatsapp", provider_user_id=from_phone)
    except Exception:
        identity = None

    linked_account_id = str((identity or {}).get("account_id") or "").strip()
    if linked_account_id:
        return linked_account_id

    try:
        lk = lookup_account(provider="wa", provider_user_id=from_phone)
    except Exception:
        lk = {"ok": False}

    if isinstance(lk, dict) and lk.get("ok"):
        return str(lk.get("account_id") or "").strip()

    return ""


def _link_failure_text(reason: str) -> str:
    reason = str(reason or "").strip().lower()
    if reason == "invalid_code":
        return "❌ Link failed. The code is invalid. Please generate a fresh WhatsApp LINK CODE on the website and send it here again."
    if reason == "used_code":
        return "❌ Link failed. That code has already been used. Please generate a fresh WhatsApp LINK CODE on the website and send it here again."
    if reason == "expired_code":
        return "❌ Link failed. That code has expired. Please generate a fresh WhatsApp LINK CODE on the website and send it here again."
    if reason == "channel_belongs_to_another_user":
        return "❌ This WhatsApp number is already linked to another account. Unlink it first or use a different number."
    return (
        "❌ Link failed. Please generate a fresh WhatsApp LINK CODE on the website and send it here again.\n"
        f"Reason: {_clip(reason)}"
    )


def _send_onboarding(from_phone: str) -> None:
    send_whatsapp_text(
        from_phone,
        "Welcome to Naija Tax Guide ✅\n\n"
        "To link this WhatsApp number to your website account:\n"
        "1) Login on the website\n"
        "2) Generate your WhatsApp LINK CODE\n"
        "3) Send the 8-character code here\n\n"
        "Example: 7K9M2H8P\n\n"
        "After linking, send your tax questions here anytime.",
    )


@bp.get("/whatsapp/webhook")
def wa_webhook_verify():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token and WA_VERIFY_TOKEN and token == WA_VERIFY_TOKEN:
        return (challenge or ""), 200
    return "Forbidden", 403


@bp.post("/whatsapp/webhook")
def wa_webhook_receive():
    raw_body = request.get_data(cache=True, as_text=False) or b""
    if not _verify_meta_signature(raw_body):
        return jsonify({"ok": False, "error": "invalid_signature"}), 403

    body = request.get_json(silent=True) or {}

    try:
        from_phone, text = _extract_message(body)
        if not from_phone:
            return jsonify({"ok": True, "ignored": True})

        # Keep shell row alive for inbound presence/history only.
        upsert_account(provider="wa", provider_user_id=from_phone, display_name=None, phone=from_phone)

        linked_account_id = _resolve_linked_account_id(from_phone)

        if not linked_account_id:
            if text and _is_link_code(text):
                attempt = consume_and_link(
                    provider="wa",
                    code=text.strip().upper(),
                    provider_user_id=from_phone,
                    display_name=None,
                    phone=from_phone,
                )
                if attempt.get("ok"):
                    send_whatsapp_text(
                        from_phone,
                        "✅ WhatsApp linked successfully!\nNow send your tax question here anytime.",
                    )
                    return jsonify(
                        {
                            "ok": True,
                            "linked": True,
                            "linked_now": True,
                            "account_id": attempt.get("account_id"),
                        }
                    )

                send_whatsapp_text(from_phone, _link_failure_text(attempt.get("reason")))
                return jsonify({"ok": True, "linked": False, "attempt": attempt})

            _send_onboarding(from_phone)
            return jsonify({"ok": True, "linked": False})

        if not text:
            send_whatsapp_text(from_phone, "Send your question as text and I will reply.")
            return jsonify({"ok": True, "linked": True, "ignored": True, "reason": "no_text"})

        if _is_link_code(text):
            send_whatsapp_text(from_phone, "✅ This WhatsApp number is already linked. You can send your tax question here anytime.")
            return jsonify({"ok": True, "linked": True, "ignored": True, "reason": "already_linked"})

        resp = ask_guarded(
            account_id=linked_account_id,
            question=text,
            lang="en",
            channel="whatsapp",
        )

        answer = str(resp.get("answer") or resp.get("message") or "").strip()
        if not answer:
            answer = "I couldn't process that right now. Please try again."

        send_whatsapp_text(from_phone, answer)
        return jsonify({"ok": True, "linked": True, "account_id": linked_account_id, "ask": resp})

    except Exception as e:
        logging.exception("WA webhook error: %s", e)
        return jsonify({"ok": True})
