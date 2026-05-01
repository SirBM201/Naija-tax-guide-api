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
from app.services.channel_credit_service import (
    get_credit_balance,
    get_credit_packages_menu,
    validate_package_number,
    create_credit_payment,
    format_balance_message
)

bp = Blueprint("whatsapp", __name__)

WA_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "").strip()

LINK_CODE_RE = re.compile(r"^[A-Z0-9]{8}$")
MENU_NUMBER_RE = re.compile(r"^[1-7]$")


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


def _send_main_menu(phone: str):
    menu = (
        "*Naija Tax Guide* ✅\n\n"
        "Reply with:\n"
        "1️⃣ – Ask a tax question\n"
        "2️⃣ – Check AI credits balance\n"
        "3️⃣ – Check current plan\n"
        "4️⃣ – Upgrade subscription\n"
        "5️⃣ – Link website account\n"
        "6️⃣ – Buy AI credits\n"
        "7️⃣ – Help / how to use\n\n"
        "You can also type your tax question directly at any time."
    )
    send_whatsapp_text(phone, menu)


def _send_help(phone: str):
    help_msg = (
        "*How to use Naija Tax Guide* 🤖\n\n"
        "• *Ask tax questions*: Just type your question naturally\n"
        "  Example: 'What is PAYE tax?' or 'When is VAT due?'\n\n"
        "• *Check credits*: Reply with 2\n\n"
        "• *Check your plan*: Reply with 3\n\n"
        "• *Upgrade subscription*: Reply with 4\n\n"
        "• *Link WhatsApp to website*: Reply with 5\n\n"
        "• *Buy AI credits*: Reply with 6\n\n"
        "• *Show this menu again*: Reply with 7\n\n"
        "Need more help? Email support@naijataxguides.com"
    )
    send_whatsapp_text(phone, help_msg)


def _send_welcome(phone: str):
    welcome = (
        "Welcome to Naija Tax Guide ✅\n\n"
        "Reply with:\n"
        "1 – Ask a tax question\n"
        "2 – Check AI credits balance\n"
        "3 – Check current plan\n"
        "4 – Upgrade subscription\n"
        "5 – Link website account\n"
        "6 – Buy AI credits\n"
        "7 – Help / how to use\n\n"
        "You can also type your tax question directly at any time."
    )
    send_whatsapp_text(phone, welcome)


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

        account_id = lk.get("account_id") or from_phone

        # Send welcome for new users with no text
        if not text:
            _send_welcome(from_phone)
            return jsonify({"ok": True})

        # Handle numbered menu options
        if MENU_NUMBER_RE.match(text):
            option = int(text)
            
            if option == 1:
                send_whatsapp_text(from_phone, "Please type your tax question and I'll answer it.")
                return jsonify({"ok": True})
            
            elif option == 2:
                # Check AI credits balance
                balance = get_credit_balance(account_id)
                send_whatsapp_text(from_phone, format_balance_message(balance))
                return jsonify({"ok": True})
            
            elif option == 3:
                send_whatsapp_text(
                    from_phone,
                    "📋 *Your Current Plan*\n\n"
                    "Plan: Free\n"
                    "AI Credits: 10/month\n"
                    "Daily Questions: Unlimited\n\n"
                    "Reply with 6 to buy more credits."
                )
                return jsonify({"ok": True})
            
            elif option == 4:
                send_whatsapp_text(
                    from_phone,
                    "💎 *Upgrade Your Plan*\n\n"
                    "Visit our website to upgrade:\n"
                    "https://www.naijataxguides.com/plans\n\n"
                    "Or reply with 6 to buy credits."
                )
                return jsonify({"ok": True})
            
            elif option == 5:
                send_whatsapp_text(
                    from_phone,
                    "🔗 *Link to Website*\n\n"
                    "1. Login to your account on our website\n"
                    "2. Go to Settings → WhatsApp Linking\n"
                    "3. Generate an 8-character code\n"
                    "4. Send that code here\n\n"
                    "Once linked, your WhatsApp will be connected to your web account!"
                )
                return jsonify({"ok": True})
            
            elif option == 6:
                # Buy AI Credits
                credit_menu = get_credit_packages_menu()
                send_whatsapp_text(from_phone, credit_menu)
                return jsonify({"ok": True})
            
            elif option == 7:
                _send_main_menu(from_phone)
                return jsonify({"ok": True})

        # Handle credit package selection (if user responded with 1-4 after seeing menu)
        if text in ["1", "2", "3", "4"]:
            package_num = int(text)
            package = validate_package_number(package_num)
            if package:
                result = create_credit_payment(account_id, package_num, "whatsapp", from_phone)
                if result.get("ok"):
                    send_whatsapp_text(from_phone, result["message"])
                else:
                    send_whatsapp_text(from_phone, f"❌ {result.get('message', 'Please try again.')}")
            else:
                send_whatsapp_text(from_phone, "❌ Invalid package number. Please select 1-4.\n\nSend 6 to see available packages.")
            return jsonify({"ok": True})

        # Handle linking code
        if LINK_CODE_RE.match(text.upper()):
            attempt = _try_consume_link_code(from_phone, text)
            if attempt.get("ok"):
                send_whatsapp_text(
                    from_phone,
                    "✅ *WhatsApp linked successfully!*\n\n"
                    "Your account is now connected to the web.\n"
                    "You can now access your history and credits from the website."
                )
                return jsonify({"ok": True, "linked": True})
            else:
                send_whatsapp_text(
                    from_phone,
                    "❌ *Invalid link code*\n\n"
                    "Please generate a new code on the website and try again.\n\n"
                    "Reply with 7 for help."
                )
                return jsonify({"ok": True, "linked": False})

        # Handle help variations
        if text.lower() in ["help", "menu", "start", "?"]:
            _send_main_menu(from_phone)
            return jsonify({"ok": True})

        # Answer tax question directly
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
                send_whatsapp_text(from_phone, "I couldn't find an answer to that question. Please try rephrasing.\n\nReply with 7 for help.")
        else:
            send_whatsapp_text(
                from_phone,
                "Sorry, I encountered an error. Please try again later.\n\nReply with 7 for help."
            )

        return jsonify({"ok": True, "answered": True})

    except Exception as e:
        logging.exception(f"WA webhook error: {e}")
        return jsonify({"ok": True})
