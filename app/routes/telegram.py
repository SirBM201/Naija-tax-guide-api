# app/routes/telegram.py
from __future__ import annotations

import os
import re
import logging
from flask import Blueprint, request, jsonify

from app.services.accounts_service import upsert_account, lookup_account
from app.core.supabase_client import supabase
from app.services.ask_service import ask_guarded
from app.services.outbound_service import send_telegram_text

bp = Blueprint("telegram", __name__)

LINK_CODE_RE = re.compile(r"^[A-Z0-9]{8}$")
MENU_RE = re.compile(r"^(7|menu|help|start)$", re.IGNORECASE)


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
                    "p_provider": "tg",
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


def _send_menu(chat_id: str):
    """Send the interactive menu"""
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
    send_telegram_text(chat_id, menu)


@bp.post("/telegram/webhook")
def tg_webhook():
    """
    Telegram webhook handler - Channel-first approach:
    - Answers tax questions immediately (no linking required)
    - Optional linking for web account integration
    - Menu support with '7', 'menu', 'help', '/start'
    """
    update = request.get_json(silent=True) or {}

    # Handle callback queries if needed
    if update.get("callback_query"):
        return jsonify({"ok": True, "ignored": True})

    msg = update.get("message") or update.get("edited_message") or {}
    if not msg:
        return jsonify({"ok": True, "ignored": True})

    chat = msg.get("chat") or {}
    chat_id = chat.get("id")

    text = (msg.get("text") or "").strip()

    user = msg.get("from") or {}
    tg_user_id = str(user.get("id") or "").strip()
    display_name = " ".join([x for x in [user.get("first_name"), user.get("last_name")] if x]) or None

    if not tg_user_id or not chat_id:
        return jsonify({"ok": True, "ignored": True})

    # Handle different message types
    if not text:
        send_telegram_text(chat_id, "Send your tax question as text and I will reply.\n\nSend '7' for menu.")
        return jsonify({"ok": True})

    # Ensure account exists for tracking (not required for answering)
    upsert_account(provider="tg", provider_user_id=tg_user_id, display_name=display_name, phone=None)
    lk = lookup_account(provider="tg", provider_user_id=tg_user_id)

    # Handle menu request (/start, 7, menu, help)
    if MENU_RE.match(text) or text.lower() == "/start":
        _send_menu(chat_id)
        return jsonify({"ok": True, "menu": True})

    # Handle linking code (OPTIONAL)
    if LINK_CODE_RE.match(text.upper()):
        attempt = _try_consume_link_code(tg_user_id, text)
        if attempt.get("ok"):
            send_telegram_text(
                chat_id,
                "✅ *Telegram linked successfully!*\n\n"
                "Your account is now connected to the web.\n"
                "You can still ask tax questions anytime.\n\n"
                "Send '7' for menu."
            )
            return jsonify({"ok": True, "linked": True, "linked_now": True})
        else:
            send_telegram_text(
                chat_id,
                "❌ *Invalid link code*\n\n"
                "Generate a new code on the website and try again.\n\n"
                "Send '7' to see the menu."
            )
            return jsonify({"ok": True, "linked": False})

    # Get account_id from lookup
    account_id = lk.get("account_id") or tg_user_id

    # Answer tax question directly (NO LINKING REQUIRED!)
    try:
        result = ask_guarded({
            "question": text,
            "account_id": account_id,
            "lang": "en",
            "channel": "telegram"
        })

        if result.get("ok"):
            answer = result.get("answer", "")
            if answer:
                send_telegram_text(chat_id, answer)
            else:
                send_telegram_text(chat_id, "I couldn't find an answer to that question. Please try rephrasing.\n\nSend '7' for menu.")
        else:
            error = result.get("error", "unknown_error")
            send_telegram_text(
                chat_id,
                f"Sorry, I encountered an error. Please try again later.\n\nSend '7' for menu."
            )

        # Add helpful tip for new users (only if not already linked)
        if not lk.get("linked"):
            send_telegram_text(
                chat_id,
                "\n💡 *Tip:* Send '7' anytime to see the menu.\n"
                "To link with your web account, send your 8-character link code."
            )

        return jsonify({"ok": True, "answered": True})

    except Exception as e:
        logging.exception(f"TG webhook error: {e}")
        send_telegram_text(
            chat_id,
            "Sorry, I encountered an error. Please try again later.\n\nSend '7' for menu."
        )
        return jsonify({"ok": True})
