from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, jsonify, request

from app.services.accounts_service import lookup_account, upsert_account
from app.services.ask_service import ask_guarded
from app.services.channel_linking_service import consume_and_link, extract_code
from app.services.outbound_service import send_whatsapp_text

bp = Blueprint("whatsapp", __name__)

WA_VERIFY_TOKEN = (os.getenv("WHATSAPP_VERIFY_TOKEN") or "").strip()
WA_APP_SECRET = (os.getenv("WHATSAPP_APP_SECRET") or "").strip()

WELCOME_TEXT = (
    "Welcome to Naija Tax Guide ✅\n\n"
    "To link this WhatsApp number to your website account:\n"
    "1) Login on the website\n"
    "2) Generate your WhatsApp LINK CODE\n"
    "3) Send the 8-character code here\n\n"
    "Example: 7K9M2H8P\n\n"
    "After linking, send your tax questions here anytime."
)

HELP_TEXT = (
    "How to use Naija Tax Guide on WhatsApp:\n\n"
    "• If this number is not linked yet, generate your WhatsApp LINK CODE on the website and send it here\n"
    "• Once linked, just send your tax question normally\n"
    "• You can send text such as:\n"
    "  - How do I register for VAT?\n"
    "  - What is PAYE?\n"
    "  - How do I file company income tax in Nigeria?\n\n"
    "If you need to relink, generate a fresh code on the website and send it here."
)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _safe_json() -> Dict[str, Any]:
    return request.get_json(silent=True) or {}



def _verify_signature(raw_body: bytes) -> bool:
    """
    Optional but strongly recommended.
    If WHATSAPP_APP_SECRET is not set, signature verification is skipped.
    """
    if not WA_APP_SECRET:
        return True

    header = _clean(request.headers.get("X-Hub-Signature-256"))
    if not header.startswith("sha256="):
        return False

    their_sig = header.split("=", 1)[1].strip()
    expected = hmac.new(
        WA_APP_SECRET.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, their_sig)



def _extract_text_from_message(msg: Dict[str, Any]) -> str:
    msg_type = _clean(msg.get("type")).lower()

    if msg_type == "text":
        return _clean(((msg.get("text") or {}).get("body")))

    if msg_type == "button":
        return _clean(((msg.get("button") or {}).get("text")))

    if msg_type == "interactive":
        interactive = msg.get("interactive") or {}
        interactive_type = _clean(interactive.get("type")).lower()

        if interactive_type == "button_reply":
            button_reply = interactive.get("button_reply") or {}
            return _clean(button_reply.get("title") or button_reply.get("id"))

        if interactive_type == "list_reply":
            list_reply = interactive.get("list_reply") or {}
            return _clean(list_reply.get("title") or list_reply.get("id"))

    return ""



def _extract_inbound_events(payload: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    Extract inbound WhatsApp user messages from Meta webhook payload.
    Ignores statuses and non-message events.
    """
    events: List[Dict[str, str]] = []

    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            messages = value.get("messages") or []

            for msg in messages:
                from_phone = _clean(msg.get("from"))
                text = _extract_text_from_message(msg)
                message_id = _clean(msg.get("id"))

                if not from_phone:
                    continue

                events.append(
                    {
                        "from_phone": from_phone,
                        "text": text,
                        "message_id": message_id,
                        "message_type": _clean(msg.get("type")).lower(),
                    }
                )

    return events



def _is_help_trigger(text: str) -> bool:
    lowered = _clean(text).lower()
    return lowered in {
        "hi",
        "hello",
        "hey",
        "menu",
        "help",
        "start",
        "/start",
    }



def _reply_unlinked(from_phone: str, text: str) -> Dict[str, Any]:
    code = extract_code(text)

    if code:
        result = consume_and_link(
            provider="wa",
            code=code,
            provider_user_id=from_phone,
            display_name=None,
            phone=from_phone,
        )

        if result.get("ok"):
            send_whatsapp_text(
                from_phone,
                "✅ WhatsApp linked successfully!\nNow send your tax question here anytime.",
            )
            return {"ok": True, "linked_now": True, "link": result}

        send_whatsapp_text(
            from_phone,
            "❌ Link failed. The code may be invalid, expired, or already used.\n"
            "Please generate a fresh WhatsApp LINK CODE on the website and send it here again.",
        )
        return {"ok": False, "linked_now": False, "error": result.get("error") or "link_failed"}

    if _is_help_trigger(text) or not _clean(text):
        send_whatsapp_text(from_phone, WELCOME_TEXT)
        return {"ok": True, "linked_now": False, "sent": "welcome"}

    send_whatsapp_text(
        from_phone,
        "This WhatsApp number is not linked yet.\n\n"
        "1) Login on the website\n"
        "2) Generate your WhatsApp LINK CODE\n"
        "3) Send the 8-character code here\n\n"
        "Example: 7K9M2H8P",
    )
    return {"ok": True, "linked_now": False, "sent": "link_prompt"}



def _reply_linked(from_phone: str, account_id: str, text: str) -> Dict[str, Any]:
    if _is_help_trigger(text):
        send_whatsapp_text(from_phone, HELP_TEXT)
        return {"ok": True, "handled": "help"}

    if not _clean(text):
        send_whatsapp_text(from_phone, "Send your tax question as text and I will reply here.")
        return {"ok": True, "handled": "no_text"}

    resp = ask_guarded(
        account_id=account_id,
        question=text,
        lang="en",
        channel="whatsapp",
        provider="wa",
        provider_user_id=from_phone,
    )

    answer = _clean(resp.get("answer") or resp.get("message"))
    if not answer:
        answer = "I couldn't process that right now. Please try again."

    send_whatsapp_text(from_phone, answer)
    return {"ok": True, "handled": "ask", "ask": resp}


@bp.get("/whatsapp/webhook")
def wa_webhook_verify():
    mode = _clean(request.args.get("hub.mode"))
    token = _clean(request.args.get("hub.verify_token"))
    challenge = _clean(request.args.get("hub.challenge"))

    if mode == "subscribe" and token and WA_VERIFY_TOKEN and token == WA_VERIFY_TOKEN:
        return challenge, 200
    return "Forbidden", 403


@bp.post("/whatsapp/webhook")
def wa_webhook_receive():
    raw_body = request.get_data(cache=True) or b""

    if not _verify_signature(raw_body):
        return jsonify({"ok": False, "error": "invalid_signature"}), 403

    payload = _safe_json()

    try:
        events = _extract_inbound_events(payload)
        if not events:
            return jsonify({"ok": True, "ignored": True, "reason": "no_messages"}), 200

        results: List[Dict[str, Any]] = []

        for event in events:
            from_phone = _clean(event.get("from_phone"))
            text = _clean(event.get("text"))

            if not from_phone:
                continue

            upsert_account(
                provider="wa",
                provider_user_id=from_phone,
                display_name=None,
                phone=from_phone,
            )

            lookup = lookup_account(provider="wa", provider_user_id=from_phone)
            if not lookup.get("ok"):
                send_whatsapp_text(from_phone, "System error. Please try again.")
                results.append(
                    {
                        "from_phone": from_phone,
                        "ok": False,
                        "error": lookup.get("error") or "lookup_failed",
                    }
                )
                continue

            if not lookup.get("linked"):
                outcome = _reply_unlinked(from_phone, text)
                results.append({"from_phone": from_phone, **outcome})
                continue

            account_id = _clean(lookup.get("account_id"))
            if not account_id:
                send_whatsapp_text(
                    from_phone,
                    "System error. Your linked account could not be resolved.",
                )
                results.append(
                    {
                        "from_phone": from_phone,
                        "ok": False,
                        "error": "missing_account_id",
                    }
                )
                continue

            outcome = _reply_linked(from_phone, account_id, text)
            results.append({"from_phone": from_phone, **outcome})

        return jsonify({"ok": True, "processed": len(results), "results": results}), 200

    except Exception as e:
        logging.exception("WA webhook error: %s", e)
        return jsonify({"ok": True, "error": "webhook_exception", "detail": repr(e)}), 200
