# app/routes/telegram.py
from __future__ import annotations

import re
import logging
from flask import Blueprint, request, jsonify

from app.services.accounts_service import upsert_account, lookup_account
from app.core.supabase_client import supabase
from app.services.ask_service import ask_guarded
from app.services.outbound_service import send_telegram_text
from app.services.channel_credit_service import (
    get_credit_balance,
    get_credit_packages_menu,
    validate_package_number,
    create_credit_payment,
    format_balance_message
)
from app.services.channel_subscription_service import (
    get_subscription_plans_menu,
    validate_plan_number,
    create_subscription_payment,
    get_user_subscription,
    format_subscription_message,
    get_user_email,
    request_email_message
)

bp = Blueprint("telegram", __name__)

LINK_CODE_RE = re.compile(r"^[A-Z0-9]{8}$")
MENU_NUMBER_RE = re.compile(r"^[1-7]$")

# Track user states for multi-step flows
user_states = {}


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
        "6️⃣ – Buy AI credits\n"
        "7️⃣ – Help / how to use\n\n"
        "You can also type your tax question directly at any time."
    )
    send_telegram_text(chat_id, menu)


def _send_help(chat_id: str):
    help_msg = (
        "*How to use Naija Tax Guide* 🤖\n\n"
        "• *Ask tax questions*: Just type your question naturally\n"
        "  Example: 'What is PAYE tax?' or 'When is VAT due?'\n\n"
        "• *Check credits*: Reply with 2\n\n"
        "• *Check your plan*: Reply with 3\n\n"
        "• *Upgrade subscription*: Reply with 4\n\n"
        "• *Link Telegram to website*: Reply with 5\n\n"
        "• *Buy AI credits*: Reply with 6\n\n"
        "• *Show this menu again*: Reply with 7\n\n"
        "Need more help? Email support@naijataxguides.com"
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
        "6 – Buy AI credits\n"
        "7 – Help / how to use\n\n"
        "You can also type your tax question directly at any time."
    )
    send_telegram_text(chat_id, welcome)


@bp.post("/telegram/webhook")
def tg_webhook():
    global user_states
    update = request.get_json(silent=True) or {}

    if update.get("callback_query"):
        return jsonify({"ok": True, "ignored": True})

    msg = update.get("message") or update.get("edited_message") or {}
    if not msg:
        return jsonify({"ok": True, "ignored": True})

    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    chat_id_str = str(chat_id)

    text = (msg.get("text") or "").strip()

    user = msg.get("from") or {}
    tg_user_id = str(user.get("id") or "").strip()
    display_name = " ".join([x for x in [user.get("first_name"), user.get("last_name")] if x]) or None

    if not tg_user_id or not chat_id:
        return jsonify({"ok": True, "ignored": True})

    # Ensure account exists
    upsert_account(provider="tg", provider_user_id=tg_user_id, display_name=display_name, phone=None)
    lk = lookup_account(provider="tg", provider_user_id=tg_user_id)

    if not lk.get("ok"):
        send_telegram_text(chat_id_str, "System error. Please try again.")
        return jsonify({"ok": True})

    account_id = lk.get("account_id") or tg_user_id
    user_state = user_states.get(chat_id_str, {})

    # Send welcome for new users with no text
    if not text:
        _send_welcome(chat_id_str)
        return jsonify({"ok": True})

    # Handle /start command
    if text.lower() == "/start":
        _send_main_menu(chat_id_str)
        return jsonify({"ok": True})

    # Handle email collection for subscription (awaiting_email state)
    if user_state.get("awaiting_email"):
        email = text.strip().lower()
        plan_num = user_state.get("pending_plan_num")
        
        if "@" in email and "." in email:
            result = create_subscription_payment(
                account_id=account_id,
                plan_num=plan_num,
                channel_type="telegram",
                provider_user_id=tg_user_id,
                email=email
            )
            
            if result.get("ok"):
                send_telegram_text(chat_id_str, result["message"])
            else:
                send_telegram_text(chat_id_str, f"❌ {result.get('message', 'Please try again.')}")
            
            # Clear state
            user_states.pop(chat_id_str, None)
        else:
            send_telegram_text(chat_id_str, "❌ Invalid email address. Please send a valid email (e.g., example@gmail.com)")
        
        return jsonify({"ok": True})

    # Handle numbered menu options
    if MENU_NUMBER_RE.match(text):
        option = int(text)
        
        if option == 1:
            send_telegram_text(chat_id_str, "Please type your tax question and I'll answer it.")
            return jsonify({"ok": True})
        
        elif option == 2:
            balance = get_credit_balance(account_id)
            send_telegram_text(chat_id_str, format_balance_message(balance))
            return jsonify({"ok": True})
        
        elif option == 3:
            current_sub = get_user_subscription(account_id)
            send_telegram_text(chat_id_str, format_subscription_message(current_sub))
            return jsonify({"ok": True})
        
        elif option == 4:
            current_sub = get_user_subscription(account_id)
            if current_sub:
                send_telegram_text(chat_id_str, format_subscription_message(current_sub))
                send_telegram_text(
                    chat_id_str,
                    "To upgrade or change your plan, please visit our website:\nhttps://www.naijataxguides.com/plans\n\nOr contact support."
                )
            else:
                plans_menu = get_subscription_plans_menu()
                send_telegram_text(chat_id_str, plans_menu)
            return jsonify({"ok": True})
        
        elif option == 5:
            send_telegram_text(
                chat_id_str,
                "🔗 *Link to Website*\n\n"
                "1. Login to your account on our website\n"
                "2. Go to Settings → Telegram Linking\n"
                "3. Generate an 8-character code\n"
                "4. Send that code here\n\n"
                "Once linked, your Telegram will be connected to your web account!"
            )
            return jsonify({"ok": True})
        
        elif option == 6:
            credit_menu = get_credit_packages_menu()
            send_telegram_text(chat_id_str, credit_menu)
            return jsonify({"ok": True})
        
        elif option == 7:
            _send_main_menu(chat_id_str)
            return jsonify({"ok": True})

    # Handle credit package selection (1-4)
    if text in ["1", "2", "3", "4"]:
        package_num = int(text)
        package = validate_package_number(package_num)
        if package:
            result = create_credit_payment(account_id, package_num, "telegram", tg_user_id)
            if result.get("ok"):
                send_telegram_text(chat_id_str, result["message"])
            else:
                send_telegram_text(chat_id_str, f"❌ {result.get('message', 'Please try again.')}")
        else:
            send_telegram_text(chat_id_str, "❌ Invalid package number. Please select 1-4.\n\nSend 6 to see available packages.")
        return jsonify({"ok": True})

    # Handle subscription plan selection (1-3)
    if text in ["1", "2", "3"]:
        plan_num = int(text)
        plan = validate_plan_number(plan_num)
        if plan:
            user_email = get_user_email(account_id)
            if user_email:
                result = create_subscription_payment(
                    account_id=account_id,
                    plan_num=plan_num,
                    channel_type="telegram",
                    provider_user_id=tg_user_id,
                    email=user_email
                )
                if result.get("ok"):
                    send_telegram_text(chat_id_str, result["message"])
                else:
                    send_telegram_text(chat_id_str, f"❌ {result.get('message', 'Please try again.')}")
            else:
                user_states[chat_id_str] = {"awaiting_email": True, "pending_plan_num": plan_num}
                send_telegram_text(chat_id_str, request_email_message())
        else:
            send_telegram_text(chat_id_str, "❌ Invalid plan number. Please select 1-3.\n\nSend 4 to see available plans.")
        return jsonify({"ok": True})

    # Handle linking code
    if LINK_CODE_RE.match(text.upper()):
        attempt = _try_consume_link_code(tg_user_id, text)
        if attempt.get("ok"):
            send_telegram_text(
                chat_id_str,
                "✅ *Telegram linked successfully!*\n\n"
                "Your account is now connected to the web.\n"
                "You can now access your history and credits from the website."
            )
            return jsonify({"ok": True, "linked": True})
        else:
            send_telegram_text(
                chat_id_str,
                "❌ *Invalid link code*\n\n"
                "Please generate a new code on the website and try again.\n\n"
                "Reply with 7 for help."
            )
            return jsonify({"ok": True, "linked": False})

    # Handle help variations
    if text.lower() in ["help", "menu", "?"]:
        _send_main_menu(chat_id_str)
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
                send_telegram_text(chat_id_str, answer)
            else:
                send_telegram_text(chat_id_str, "I couldn't find an answer to that question. Please try rephrasing.\n\nReply with 7 for help.")
        else:
            send_telegram_text(
                chat_id_str,
                "Sorry, I encountered an error. Please try again later.\n\nReply with 7 for help."
            )

        return jsonify({"ok": True, "answered": True})

    except Exception as e:
        logging.exception(f"TG webhook error: {e}")
        send_telegram_text(chat_id_str, "Sorry, I encountered an error. Please try again later.")
        return jsonify({"ok": True})
