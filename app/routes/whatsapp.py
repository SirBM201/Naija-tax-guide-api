# app/routes/whatsapp.py
from __future__ import annotations

import os
import re
import logging
from flask import Blueprint, request, jsonify

from app.services.accounts_service import upsert_account, lookup_account
from app.core.supabase_client import supabase
from app.services.ask_service import ask_guarded
from app.services.outbound_service import send_whatsapp_text

bp = Blueprint("whatsapp", __name__)

WA_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "").strip()

LINK_CODE_RE = re.compile(r"^[A-Z0-9]{8}$")
MENU_RE = re.compile(r"^(7|menu|help)$", re.IGNORECASE)


def _extract_message(body: dict) -> tuple[str, str]:
    entry = (body.get("entry") or [None])[0] or {}
    changes = (entry.get("changes") or [None])[0] or {}
    value = changes.get("value") or {}
    messages = value.get("messages") or []
    if not messages:
        return "", ""

    msg = messages[0]
    from_phone = (msg.get("from") or "").strip()

    msg_type = msg.get("type")
    text = ""
    if msg_type == "text":
        text = ((msg.get("text") or {}).get("body") or "").strip()

    return from_phone, text


def _try_consume_link_code(provider_user_id: str, raw_text: str) -> dict:
    code = (raw_text or "").strip().upper()
    if not LINK_CODE_RE.match(code):
        return {"ok": False, "reason": "not_a_code"}

    try:
        res = (
            supabase()
            .rpc(
                "consume_link_token",
                {
                    "p_provider": "wa",
                    "p_code": code,
                    "p_provider_user_id": provider_user_id,
                },
            )
            .execute()
        )
    except Exception as e:
        return {"ok": False, "reason": "rpc_error", "error": str(e)}

    row = (res.data or [None])[0]
    if not row:
        return {"ok": False, "reason": "no_rpc_row"}

    if row.get("ok") is True and row.get("auth_user_id"):
        return {"ok": True, "auth_user_id": row.get("auth_user_id")}

    return {"ok": False, "reason": row.get("reason") or "consume_failed", "rpc": row}


def _send_menu(phone: str):
    menu = (
        "📋 *Naija Tax Guide Menu*\n\n"
        "7️⃣ - Show this menu\n\n"
        "*Ask tax questions directly*\n"
        "Just type your tax question and I'll answer!\n\n"
        "Examples:\n"
        "• What is PAYE tax?\n"
        "• When is VAT due?\n"
        "• How to calculate CIT?\n\n"
        "*To link with your web account:*\n"
        "1. Login on website\n"
        "2. Generate LINK CODE\n"
        "3. Send the 8-character code here"
    )
    send_whatsapp_text(phone, menu)


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
    body = request.get_json(silent=True) or {}

    try:
        from_phone, text = _extract_message(body)
        if not from_phone:
            return jsonify({"ok": True, "ignored": True})

        # Ensure account exists
        upsert_account(provider="wa", provider_user_id=from_phone, display_name=None, phone=from_phone)

        lk = lookup_account(provider="wa", provider_user_id=from_phone)
        if not lk.get("ok"):
            send_whatsapp_text(from_phone, "System error. Please try again.")
            return jsonify({"ok": True})

        # Handle menu request
        if MENU_RE.match(text):
            _send_menu(from_phone)
            return jsonify({"ok": True, "menu": True})

        # Handle linking code (optional)
        if LINK_CODE_RE.match(text.upper()):
            attempt = _try_consume_link_code(from_phone, text)
            if attempt.get("ok"):
                send_whatsapp_text(
                    from_phone,
                    "✅ *WhatsApp linked successfully!*\n\n"
                    "Your account is now connected to the web.\n"
                    "You can still ask tax questions anytime."
                )
                return jsonify({"ok": True, "linked": True})
            else:
                send_whatsapp_text(
                    from_phone,
                    "❌ *Invalid link code*\n\n"
                    "Generate a new code on the website and try again.\n\n"
                    "Reply with '7' to see the menu."
                )
                return jsonify({"ok": True, "linked": False})

        # Answer tax question directly
        account_id = lk.get("account_id") or from_phone
        
        # Call ask_guarded with the correct parameters
        result = ask_guarded({
            "question": text,
            "account_id": account_id,
            "lang": "en",
            "channel": "whatsapp"
        })

        if result.get("ok"):
            answer = result.get("answer", "")
            if answer:
                send_whatsapp_text(from_phone, answer)
            else:
                send_whatsapp_text(from_phone, "I couldn't find an answer to that question. Please try rephrasing.")
        else:
            error = result.get("error", "unknown_error")
            send_whatsapp_text(
                from_phone,
                f"Sorry, I encountered an error. Please try again later.\n\nReply with '7' to see the menu."
            )

        return jsonify({"ok": True, "answered": True})

    except Exception as e:
        logging.exception(f"WA webhook error: {e}")
        send_whatsapp_text(from_phone, f"Sorry, an error occurred. Please try again later.")
        return jsonify({"ok": True})
