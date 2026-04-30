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
MENU_NUMBER_RE = re.compile(r"^[1-6]$")


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


def _send_main_menu(chat_id: str):
    menu = (
        "*Naija Tax Guide* ✅\n\n"
        "Reply with:\n"
        "1️⃣ – Ask a tax question\n"
        "2️⃣ – Check AI credits balance\n"
        "3️⃣ – Check current plan\n"
        "4️⃣ – Upgrade subscription\n"
        "5️⃣ – Link website account\n"
        "6️⃣ – Help / how to use this bot\n\n"
        "You can also type your tax question directly at any time."
    )
    send_telegram_text(chat_id, menu)


def _send_help(chat_id: str):
    help_msg = (
        "*How to use Naija Tax Guide* 🤖\n\n"
        "• *Ask tax questions*: Just type your question naturally\n"
        "  Example: 'What is PAYE tax?' or 'When is VAT due?'\n\n"
        "• *Get your AI credits balance*: Reply with 2\n\n"
        "• *Check your plan*: Reply with 3\n\n"
        "• *Upgrade subscription*: Reply with 4\n\n"
        "• *Link Telegram to website*: Reply with 5\n\n"
        "• *Show this menu again*: Reply with 6\n\n"
        "Need more help? Visit our website or contact support."
    )
    send_telegram_text(chat_id, help_msg)


def _send_welcome(chat_id: str):
    welcome = (
        "Welcome to Naija Tax Guide ✅\n\n"
        "Reply with:\n"
        "1 – Ask a tax question\n"
        "2 – Check AI credits balance\n"
        "3 – Check current plan\n"
        "4 – Upgrade subscription\n"
        "5 – Link website account\n"
        "6 – Help / how to use this bot\n\n"
        "You can also type your tax question directly at any time."
    )
    send_telegram_text(chat_id, welcome)


@bp.post("/telegram/webhook")
def tg_webhook():
    update = request.get_json(silent=True) or {}

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

    # Ensure account exists
    upsert_account(provider="tg", provider_user_id=tg_user_id, display_name=display_name, phone=None)
    lk = lookup_account(provider="tg", provider_user_id=tg_user_id)

    account_id = lk.get("account_id") or tg_user_id

    # Send welcome for new users with no text
    if not text:
        _send_welcome(chat_id)
        return jsonify({"ok": True})

    # Handle numbered menu options
    if MENU_NUMBER_RE.match(text):
        option = int(text)
        
        if option == 1:
            send_telegram_text(chat_id, "Please type your tax question and I'll answer it.")
            return jsonify({"ok": True})
        
        elif option == 2:
            send_telegram_text(chat_id, "🔍 *AI Credits Balance*\n\nYou have 0 credits remaining.\n\nTo get more credits, upgrade your plan or purchase additional credits on the website.")
            return jsonify({"ok": True})
        
        elif option == 3:
            send_telegram_text(chat_id, "📋 *Your Current Plan*\n\nPlan: Free\nAI Credits: 0/month\nDaily Questions: Limited\n\nUpgrade for more features and unlimited questions!")
            return jsonify({"ok": True})
        
        elif option == 4:
            send_telegram_text(chat_id, "💎 *Upgrade Your Plan*\n\nVisit our website to upgrade:\nhttps://www.naijataxguides.com/plans")
            return jsonify({"ok": True})
        
        elif option == 5:
            send_telegram_text(
                chat_id,
                "🔗 *Link to Website*\n\n"
                "1. Login to your account on our website\n"
                "2. Go to Settings → Telegram Linking\n"
                "3. Generate an 8-character code\n"
                "4. Send that code here\n\n"
                "Once linked, your Telegram will be connected to your web account!"
            )
            return jsonify({"ok": True})
        
        elif option == 6:
            _send_help(chat_id)
            return jsonify({"ok": True})

    # Handle linking code
    if LINK_CODE_RE.match(text.upper()):
        attempt = _try_consume_link_code(tg_user_id, text)
        if attempt.get("ok"):
            send_telegram_text(
                chat_id,
                "✅ *Telegram linked successfully!*\n\n"
                "Your account is now connected to the web."
            )
            return jsonify({"ok": True, "linked": True})
        else:
            send_telegram_text(
                chat_id,
                "❌ *Invalid link code*\n\n"
                "Please generate a new code on the website and try again.\n\n"
                "Reply with 6 for help."
            )
            return jsonify({"ok": True, "linked": False})

    # Handle help variations
    if text.lower() in ["help", "menu", "start", "/start", "?"]:
        _send_main_menu(chat_id)
        return jsonify({"ok": True})

    # Answer tax question directly
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
                send_telegram_text(chat_id, "I couldn't find an answer to that question. Please try rephrasing.\n\nReply with 6 for help.")
        else:
            send_telegram_text(chat_id, "Sorry, I encountered an error. Please try again later.\n\nReply with 6 for help.")

        return jsonify({"ok": True, "answered": True})

    except Exception as e:
        logging.exception(f"TG webhook error: {e}")
        send_telegram_text(chat_id, "Sorry, I encountered an error. Please try again later.")
        return jsonify({"ok": True})
