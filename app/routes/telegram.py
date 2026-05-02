# app/routes/telegram.py
from __future__ import annotations

import re
import logging
from datetime import datetime
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
    get_plans_list_menu,
    validate_plan_number,
    create_subscription_payment,
    get_user_subscription,
    format_subscription_message,
    get_user_email,
    request_email_message,
    detect_plan_from_text,
    has_active_subscription,
    activate_subscription
)
from app.services.tax_filing_service import (
    save_filing_draft,
    get_filing_draft,
    delete_filing_draft,
    submit_tax_filing,
    get_user_filings,
    get_filing_by_reference
)
from app.services.tax_calculator import calculate_tax

bp = Blueprint("telegram", __name__)

LINK_CODE_RE = re.compile(r"^[A-Z0-9]{8}$")
MENU_NUMBER_RE = re.compile(r"^[1-8]$")

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
        "*🤖 Naija Tax Guide*\n\n"
        "Reply with:\n"
        "1️⃣ - Ask a tax question\n"
        "2️⃣ - Check AI credits balance\n"
        "3️⃣ - Check my subscription plan\n"
        "4️⃣ - View subscription plans\n"
        "5️⃣ - Link to website account\n"
        "6️⃣ - Buy AI credits\n"
        "7️⃣ - Tax filing & management\n"
        "8️⃣ - Help / Menu\n\n"
        "💡 You can also type your tax question directly!"
    )
    send_telegram_text(chat_id, menu)


def _send_tax_menu(chat_id: str):
    menu = (
        "*📋 TAX FILING & MANAGEMENT*\n\n"
        "Reply with:\n"
        "🇵 - File PAYE Tax\n"
        "🇻 - File VAT\n"
        "🇨 - File CIT (Company Tax)\n"
        "📜 - View my filing history\n"
        "📅 - View tax deadlines\n"
        "🔙 - Back to main menu\n\n"
        "Type /paye, /vat, or /cit to start filing."
    )
    send_telegram_text(chat_id, menu)


def _send_help(chat_id: str):
    help_msg = (
        "*📖 Help Guide*\n\n"
        "• *Ask tax questions*: Type your question naturally\n"
        "  Example: 'What is PAYE tax?'\n\n"
        "• *Check credits*: Reply 2\n\n"
        "• *View subscription*: Reply 3\n\n"
        "• *View/upgrade plans*: Reply 4\n\n"
        "• *Link to website*: Reply 5\n\n"
        "• *Buy credits*: Reply 6\n\n"
        "• *File taxes*: Reply 7 then choose tax type\n\n"
        "• *Show menu*: Reply 8\n\n"
        "Need help? Email support@naijataxguides.com"
    )
    send_telegram_text(chat_id, help_msg)


def _send_welcome(chat_id: str):
    welcome = (
        "*Welcome to Naija Tax Guide!* ✅\n\n"
        "I'm your AI tax assistant for Nigerian taxes.\n\n"
        "Reply with:\n"
        "1️⃣ - Ask a tax question\n"
        "2️⃣ - Check AI credits\n"
        "3️⃣ - View my plan\n"
        "4️⃣ - View subscription plans\n"
        "5️⃣ - Link website account\n"
        "6️⃣ - Buy AI credits\n"
        "7️⃣ - File taxes\n"
        "8️⃣ - Help\n\n"
        "Or just type your tax question!"
    )
    send_telegram_text(chat_id, welcome)


def _handle_paye_filing_step(chat_id: str, account_id: str, user_state: dict, text: str):
    """Handle PAYE filing guided flow"""
    step = user_state.get("step", 1)
    draft = user_state.get("draft", {})
    inputs = draft.get("inputs", {})
    
    if step == 1:
        try:
            amount = float(text.replace(",", "").replace("₦", "").strip())
            inputs["monthly_gross_income"] = amount
            save_filing_draft(account_id, "paye", inputs, [], step + 1)
            user_states[chat_id] = {"filing_type": "paye", "step": 2, "draft": {"inputs": inputs}}
            send_telegram_text(chat_id, f"✅ Received: ₦{amount:,.2f}\n\n📋 Step 2 of 4: Pension Contribution\nEnter your monthly pension contribution (usually 8% of gross income):")
        except ValueError:
            send_telegram_text(chat_id, "❌ Please enter a valid amount (e.g., 750000)")
    
    elif step == 2:
        try:
            amount = float(text.replace(",", "").replace("₦", "").strip())
            inputs["pension_contribution"] = amount
            save_filing_draft(account_id, "paye", inputs, [], step + 1)
            user_states[chat_id] = {"filing_type": "paye", "step": 3, "draft": {"inputs": inputs}}
            send_telegram_text(chat_id, f"✅ Received: ₦{amount:,.2f}\n\n📋 Step 3 of 4: NHF Contribution\nEnter your NHF contribution (if any, or 0):")
        except ValueError:
            send_telegram_text(chat_id, "❌ Please enter a valid amount")
    
    elif step == 3:
        try:
            amount = float(text.replace(",", "").replace("₦", "").strip())
            inputs["nhf"] = amount
            save_filing_draft(account_id, "paye", inputs, [], step + 1)
            
            calc = calculate_tax("paye", inputs)
            monthly_tax = calc.get("monthly_tax_payable", 0)
            annual_tax = calc.get("annual_tax_payable", 0)
            
            preview = (f"📋 *PAYE Filing Summary*\n\n"
                       f"• Monthly Gross Income: ₦{inputs.get('monthly_gross_income', 0):,.2f}\n"
                       f"• Pension Contribution: ₦{inputs.get('pension_contribution', 0):,.2f}\n"
                       f"• NHF Contribution: ₦{inputs.get('nhf', 0):,.2f}\n"
                       f"• Annual Taxable Income: ₦{calc.get('chargeable_income', 0):,.2f}\n"
                       f"• *Annual Tax Payable: ₦{annual_tax:,.2f}*\n"
                       f"• *Monthly Tax Deduction: ₦{monthly_tax:,.2f}*\n\n"
                       f"Reply with 'confirm' to submit, or 'cancel' to abort")
            
            user_states[chat_id] = {"filing_type": "paye", "step": 4, "draft": {"inputs": inputs}, "calculation": calc}
            send_telegram_text(chat_id, preview)
        except ValueError:
            send_telegram_text(chat_id, "❌ Please enter a valid amount")
    
    elif step == 4:
        if text.lower() == "confirm":
            result = submit_tax_filing(account_id, "paye", inputs, [])
            if result.get("ok"):
                calc = result.get("calculation", {})
                monthly_tax = calc.get("monthly_tax_payable", 0)
                reference = result.get("reference", "N/A")
                submitted_at = result.get("submitted_at", datetime.now().isoformat())
                
                success_msg = (f"✅ *PAYE Filing Submitted!*\n\n"
                               f"📋 Reference: {reference}\n"
                               f"📅 Date: {datetime.fromisoformat(submitted_at).strftime('%d %B %Y, %H:%M')}\n"
                               f"💰 Monthly Tax: ₦{monthly_tax:,.2f}\n\n"
                               f"📎 You can download your receipt from the web dashboard.\n"
                               f"Reply with /history to see all filings.")
                
                send_telegram_text(chat_id, success_msg)
                user_states.pop(chat_id, None)
                delete_filing_draft(account_id, "paye")
            else:
                send_telegram_text(chat_id, f"❌ Filing failed: {result.get('error', 'Unknown error')}")
        elif text.lower() == "cancel":
            delete_filing_draft(account_id, "paye")
            user_states.pop(chat_id, None)
            send_telegram_text(chat_id, "❌ Filing cancelled. Reply with /menu to see options.")
        else:
            send_telegram_text(chat_id, "Reply with 'confirm' to submit or 'cancel' to abort")
    
    return True


def _handle_vat_filing_step(chat_id: str, account_id: str, user_state: dict, text: str):
    """Handle VAT filing guided flow"""
    step = user_state.get("step", 1)
    draft = user_state.get("draft", {})
    inputs = draft.get("inputs", {})
    
    if step == 1:
        try:
            amount = float(text.replace(",", "").replace("₦", "").strip())
            inputs["taxable_supplies"] = amount
            save_filing_draft(account_id, "vat", inputs, [], step + 1)
            user_states[chat_id] = {"filing_type": "vat", "step": 2, "draft": {"inputs": inputs}}
            send_telegram_text(chat_id, f"✅ Received: ₦{amount:,.2f}\n\n📋 Step 2 of 3: Input VAT\nEnter your input VAT (VAT paid on purchases):")
        except ValueError:
            send_telegram_text(chat_id, "❌ Please enter a valid amount")
    
    elif step == 2:
        try:
            amount = float(text.replace(",", "").replace("₦", "").strip())
            inputs["input_vat"] = amount
            save_filing_draft(account_id, "vat", inputs, [], step + 1)
            
            calc = calculate_tax("vat", inputs)
            vat_payable = calc.get("vat_payable", 0)
            
            preview = (f"📋 *VAT Filing Summary*\n\n"
                       f"• Taxable Supplies: ₦{inputs.get('taxable_supplies', 0):,.2f}\n"
                       f"• Input VAT: ₦{inputs.get('input_vat', 0):,.2f}\n"
                       f"• Output VAT (7.5%): ₦{calc.get('output_vat', 0):,.2f}\n"
                       f"• *VAT Payable: ₦{vat_payable:,.2f}*\n\n"
                       f"Reply with 'confirm' to submit, or 'cancel' to abort")
            
            user_states[chat_id] = {"filing_type": "vat", "step": 3, "draft": {"inputs": inputs}, "calculation": calc}
            send_telegram_text(chat_id, preview)
        except ValueError:
            send_telegram_text(chat_id, "❌ Please enter a valid amount")
    
    elif step == 3:
        if text.lower() == "confirm":
            result = submit_tax_filing(account_id, "vat", inputs, [])
            if result.get("ok"):
                calc = result.get("calculation", {})
                vat_payable = calc.get("vat_payable", 0)
                reference = result.get("reference", "N/A")
                submitted_at = result.get("submitted_at", datetime.now().isoformat())
                
                success_msg = (f"✅ *VAT Filing Submitted!*\n\n"
                               f"📋 Reference: {reference}\n"
                               f"📅 Date: {datetime.fromisoformat(submitted_at).strftime('%d %B %Y, %H:%M')}\n"
                               f"💰 VAT Payable: ₦{vat_payable:,.2f}\n\n"
                               f"📎 You can download your receipt from the web dashboard.\n"
                               f"Reply with /history to see all filings.")
                
                send_telegram_text(chat_id, success_msg)
                user_states.pop(chat_id, None)
                delete_filing_draft(account_id, "vat")
            else:
                send_telegram_text(chat_id, f"❌ Filing failed: {result.get('error', 'Unknown error')}")
        elif text.lower() == "cancel":
            delete_filing_draft(account_id, "vat")
            user_states.pop(chat_id, None)
            send_telegram_text(chat_id, "❌ Filing cancelled. Reply with /menu to see options.")
        else:
            send_telegram_text(chat_id, "Reply with 'confirm' to submit or 'cancel' to abort")
    
    return True


def _handle_cit_filing_step(chat_id: str, account_id: str, user_state: dict, text: str):
    """Handle CIT filing guided flow"""
    step = user_state.get("step", 1)
    draft = user_state.get("draft", {})
    inputs = draft.get("inputs", {})
    
    if step == 1:
        try:
            amount = float(text.replace(",", "").replace("₦", "").strip())
            inputs["gross_profit"] = amount
            save_filing_draft(account_id, "cit", inputs, [], step + 1)
            user_states[chat_id] = {"filing_type": "cit", "step": 2, "draft": {"inputs": inputs}}
            send_telegram_text(chat_id, f"✅ Received: ₦{amount:,.2f}\n\n📋 Step 2 of 3: Allowable Expenses\nEnter your allowable expenses:")
        except ValueError:
            send_telegram_text(chat_id, "❌ Please enter a valid amount")
    
    elif step == 2:
        try:
            amount = float(text.replace(",", "").replace("₦", "").strip())
            inputs["allowable_expenses"] = amount
            save_filing_draft(account_id, "cit", inputs, [], step + 1)
            
            calc = calculate_tax("cit", inputs)
            cit_payable = calc.get("cit_payable", 0)
            company_size = calc.get("company_size", "N/A")
            rate = calc.get("applicable_rate", 0)
            
            preview = (f"📋 *CIT Filing Summary*\n\n"
                       f"• Gross Profit: ₦{inputs.get('gross_profit', 0):,.2f}\n"
                       f"• Allowable Expenses: ₦{inputs.get('allowable_expenses', 0):,.2f}\n"
                       f"• Assessable Profit: ₦{calc.get('assessable_profit', 0):,.2f}\n"
                       f"• Company Size: {company_size.title()}\n"
                       f"• Applicable Rate: {rate}%\n"
                       f"• *CIT Payable: ₦{cit_payable:,.2f}*\n\n"
                       f"Reply with 'confirm' to submit, or 'cancel' to abort")
            
            user_states[chat_id] = {"filing_type": "cit", "step": 3, "draft": {"inputs": inputs}, "calculation": calc}
            send_telegram_text(chat_id, preview)
        except ValueError:
            send_telegram_text(chat_id, "❌ Please enter a valid amount")
    
    elif step == 3:
        if text.lower() == "confirm":
            result = submit_tax_filing(account_id, "cit", inputs, [])
            if result.get("ok"):
                calc = result.get("calculation", {})
                cit_payable = calc.get("cit_payable", 0)
                reference = result.get("reference", "N/A")
                submitted_at = result.get("submitted_at", datetime.now().isoformat())
                
                success_msg = (f"✅ *CIT Filing Submitted!*\n\n"
                               f"📋 Reference: {reference}\n"
                               f"📅 Date: {datetime.fromisoformat(submitted_at).strftime('%d %B %Y, %H:%M')}\n"
                               f"💰 CIT Payable: ₦{cit_payable:,.2f}\n\n"
                               f"📎 You can download your receipt from the web dashboard.\n"
                               f"Reply with /history to see all filings.")
                
                send_telegram_text(chat_id, success_msg)
                user_states.pop(chat_id, None)
                delete_filing_draft(account_id, "cit")
            else:
                send_telegram_text(chat_id, f"❌ Filing failed: {result.get('error', 'Unknown error')}")
        elif text.lower() == "cancel":
            delete_filing_draft(account_id, "cit")
            user_states.pop(chat_id, None)
            send_telegram_text(chat_id, "❌ Filing cancelled. Reply with /menu to see options.")
        else:
            send_telegram_text(chat_id, "Reply with 'confirm' to submit or 'cancel' to abort")
    
    return True


def _handle_tax_filing_command(chat_id: str, account_id: str, text: str):
    """Handle tax filing commands"""
    text_lower = text.lower().strip()
    
    if text_lower in ["/paye", "file paye", "file paye tax", "paye"]:
        user_states[chat_id] = {"filing_type": "paye", "step": 1, "draft": {"inputs": {}}}
        send_telegram_text(chat_id, "📋 *PAYE Tax Filing - Step 1 of 4*\n\nPlease provide your monthly gross income:\n(Example: 750000)")
        return True
    
    elif text_lower in ["/vat", "file vat", "file vat tax", "vat"]:
        user_states[chat_id] = {"filing_type": "vat", "step": 1, "draft": {"inputs": {}}}
        send_telegram_text(chat_id, "📋 *VAT Filing - Step 1 of 3*\n\nEnter your total taxable supplies for the period:\n(Example: 5000000)")
        return True
    
    elif text_lower in ["/cit", "file cit", "file cit tax", "file company tax", "cit"]:
        user_states[chat_id] = {"filing_type": "cit", "step": 1, "draft": {"inputs": {}}}
        send_telegram_text(chat_id, "📋 *CIT Filing - Step 1 of 3*\n\nEnter your gross profit for the period:\n(Example: 10000000)")
        return True
    
    elif text_lower in ["/history", "history", "my filings", "filing history"]:
        filings = get_user_filings(account_id, limit=10)
        if filings:
            msg = "📋 *Your Tax Filings*\n\n"
            for f in filings[:5]:
                msg += f"• *{f.get('tax_type', '').upper()}*: {f.get('reference', 'N/A')}\n"
                msg += f"  Status: {f.get('status', 'N/A')}\n"
                msg += f"  Date: {f.get('submitted_at', '')[:10] if f.get('submitted_at') else 'N/A'}\n\n"
            if len(filings) > 5:
                msg += f"\n+ {len(filings) - 5} more. Visit web for full history."
            send_telegram_text(chat_id, msg)
        else:
            send_telegram_text(chat_id, "📋 No tax filings found. Reply with /paye to file your first tax.")
        return True
    
    elif text_lower in ["/deadlines", "deadlines", "tax deadlines", "filing deadlines"]:
        send_telegram_text(chat_id, "📅 *Tax Deadlines*\n\n"
                           "• PAYE: Monthly by 10th\n"
                           "• VAT: Monthly by 21st\n"
                           "• CIT: 6 months after year end\n"
                           "• Annual Returns: March 31st\n\n"
                           "Set reminders in your web dashboard.")
        return True
    
    elif text_lower in ["/menu", "menu", "/start"]:
        _send_main_menu(chat_id)
        return True
    
    return False


def _handle_continue_filing(chat_id: str, account_id: str, text: str):
    """Continue an in-progress filing"""
    user_state = user_states.get(chat_id, {})
    filing_type = user_state.get("filing_type")
    
    if filing_type == "paye":
        return _handle_paye_filing_step(chat_id, account_id, user_state, text)
    elif filing_type == "vat":
        return _handle_vat_filing_step(chat_id, account_id, user_state, text)
    elif filing_type == "cit":
        return _handle_cit_filing_step(chat_id, account_id, user_state, text)
    
    return False


@bp.route("/telegram/webhook", methods=["POST"])
def tg_webhook():
    """Handle Telegram webhook - POST only"""
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

    # Handle email collection for subscription
    if user_state.get("awaiting_email"):
        email = text.strip().lower()
        pending_plan = user_state.get("pending_plan")
        
        if email == "cancel" or email == "0":
            user_states.pop(chat_id_str, None)
            send_telegram_text(chat_id_str, "❌ Subscription cancelled. Reply with 4 to see plans.")
            return jsonify({"ok": True})
        
        if "@" in email and "." in email:
            result = create_subscription_payment(
                account_id=account_id,
                plan=pending_plan,
                channel_type="telegram",
                provider_user_id=tg_user_id,
                email=email
            )
            
            if result.get("ok"):
                send_telegram_text(chat_id_str, result["message"])
            else:
                send_telegram_text(chat_id_str, f"❌ {result.get('message', 'Please try again.')}")
            
            user_states.pop(chat_id_str, None)
        else:
            send_telegram_text(chat_id_str, "❌ Invalid email. Send a valid email or 'cancel' to abort.")
        return jsonify({"ok": True})

    # Handle in-progress filing continuation
    if user_state.get("filing_type") and user_state.get("step"):
        _handle_continue_filing(chat_id_str, account_id, text)
        return jsonify({"ok": True})

    # Handle tax filing commands
    if _handle_tax_filing_command(chat_id_str, account_id, text):
        return jsonify({"ok": True})

    # Check if user has active subscription
    has_subscription = has_active_subscription(account_id)
    
    # Handle numbered menu options
    if MENU_NUMBER_RE.match(text):
        option = int(text)
        
        if option == 1:
            send_telegram_text(chat_id_str, "💬 Please type your tax question and I'll answer it.")
            return jsonify({"ok": True})
        
        elif option == 2:
            if has_subscription:
                sub = get_user_subscription(account_id)
                send_telegram_text(
                    chat_id_str,
                    f"💎 *UNLIMITED AI ACCESS* ✅\n\n"
                    f"You have an active subscription.\n\n"
                    f"✨ No credit limits! Ask as many tax questions as you want.\n\n"
                    f"Reply with 3 to view your plan details."
                )
            else:
                balance = get_credit_balance(account_id)
                send_telegram_text(chat_id_str, format_balance_message(balance))
            return jsonify({"ok": True})
        
        elif option == 3:
            message = format_subscription_message(account_id)
            send_telegram_text(chat_id_str, message)
            return jsonify({"ok": True})
        
        elif option == 4:
            plans_menu = get_plans_list_menu()
            send_telegram_text(chat_id_str, plans_menu)
            return jsonify({"ok": True})
        
        elif option == 5:
            send_telegram_text(
                chat_id_str,
                "🔗 *Link to Website*\n\n"
                "1. Login on our website\n"
                "2. Go to Settings → Telegram Linking\n"
                "3. Generate an 8-character code\n"
                "4. Send the code here\n\n"
                "Once linked, your Telegram connects to your web account!"
            )
            return jsonify({"ok": True})
        
        elif option == 6:
            if has_subscription:
                send_telegram_text(
                    chat_id_str,
                    "✨ You have an active subscription with UNLIMITED credits!\n\n"
                    "No need to buy credits.\n\n"
                    "Reply with 3 to view your plan details."
                )
            else:
                credit_menu = get_credit_packages_menu()
                send_telegram_text(chat_id_str, credit_menu)
            return jsonify({"ok": True})
        
        elif option == 7:
            _send_tax_menu(chat_id_str)
            return jsonify({"ok": True})
        
        elif option == 8:
            _send_main_menu(chat_id_str)
            return jsonify({"ok": True})

    # Handle single-character tax menu options (P, V, C, etc.)
    if text.upper() == "P":
        user_states[chat_id_str] = {"filing_type": "paye", "step": 1, "draft": {"inputs": {}}}
        send_telegram_text(chat_id_str, "📋 *PAYE Tax Filing - Step 1 of 4*\n\nPlease provide your monthly gross income:\n(Example: 750000)")
        return jsonify({"ok": True})
    
    elif text.upper() == "V":
        user_states[chat_id_str] = {"filing_type": "vat", "step": 1, "draft": {"inputs": {}}}
        send_telegram_text(chat_id_str, "📋 *VAT Filing - Step 1 of 3*\n\nEnter your total taxable supplies for the period:\n(Example: 5000000)")
        return jsonify({"ok": True})
    
    elif text.upper() == "C":
        user_states[chat_id_str] = {"filing_type": "cit", "step": 1, "draft": {"inputs": {}}}
        send_telegram_text(chat_id_str, "📋 *CIT Filing - Step 1 of 3*\n\nEnter your gross profit for the period:\n(Example: 10000000)")
        return jsonify({"ok": True})
    
    elif text.lower() in ["history", "📜"]:
        filings = get_user_filings(account_id, limit=10)
        if filings:
            msg = "📋 *Your Tax Filings*\n\n"
            for f in filings[:5]:
                msg += f"• *{f.get('tax_type', '').upper()}*: {f.get('reference', 'N/A')}\n"
                msg += f"  Status: {f.get('status', 'N/A')}\n"
                msg += f"  Date: {f.get('submitted_at', '')[:10] if f.get('submitted_at') else 'N/A'}\n\n"
            if len(filings) > 5:
                msg += f"\n+ {len(filings) - 5} more. Visit web for full history."
            send_telegram_text(chat_id_str, msg)
        else:
            send_telegram_text(chat_id_str, "📋 No tax filings found. Reply with P to file PAYE tax.")
        return jsonify({"ok": True})
    
    elif text.lower() in ["deadlines", "📅"]:
        send_telegram_text(chat_id_str, "📅 *Tax Deadlines*\n\n"
                           "• PAYE: Monthly by 10th\n"
                           "• VAT: Monthly by 21st\n"
                           "• CIT: 6 months after year end\n"
                           "• Annual Returns: March 31st\n\n"
                           "Set reminders in your web dashboard.")
        return jsonify({"ok": True})
    
    elif text.lower() in ["back", "🔙", "/menu"]:
        _send_main_menu(chat_id_str)
        return jsonify({"ok": True})

    # Handle credit package selection (1-4)
    if not has_subscription and text in ["1", "2", "3", "4"]:
        package_num = int(text)
        package = validate_package_number(package_num)
        if package:
            result = create_credit_payment(account_id, package_num, "telegram", tg_user_id)
            if result.get("ok"):
                send_telegram_text(chat_id_str, result["message"])
            else:
                send_telegram_text(chat_id_str, f"❌ {result.get('message', 'Please try again.')}")
        else:
            send_telegram_text(chat_id_str, "❌ Invalid package. Send 6 to see packages.")
        return jsonify({"ok": True})

    # Handle subscription plan selection (1-9)
    if text.isdigit() and 1 <= int(text) <= 9:
        plan_num = int(text)
        plan = validate_plan_number(plan_num)
        if plan:
            user_email = get_user_email(account_id)
            if user_email:
                result = create_subscription_payment(
                    account_id=account_id,
                    plan=plan,
                    channel_type="telegram",
                    provider_user_id=tg_user_id,
                    email=user_email
                )
                if result.get("ok"):
                    send_telegram_text(chat_id_str, result["message"])
                else:
                    send_telegram_text(chat_id_str, f"❌ {result.get('message', 'Please try again.')}")
            else:
                user_states[chat_id_str] = {"awaiting_email": True, "pending_plan": plan}
                send_telegram_text(chat_id_str, request_email_message())
        else:
            send_telegram_text(chat_id_str, "❌ Invalid plan number. Send 4 to see plans.")
        return jsonify({"ok": True})

    # Handle linking code
    if LINK_CODE_RE.match(text.upper()):
        attempt = _try_consume_link_code(tg_user_id, text)
        if attempt.get("ok"):
            send_telegram_text(
                chat_id_str,
                "✅ *Telegram linked successfully!*\n\n"
                "Your account is now connected to the web."
            )
            return jsonify({"ok": True, "linked": True})
        else:
            send_telegram_text(
                chat_id_str,
                "❌ *Invalid link code*\n\n"
                "Generate a new code on the website.\n\n"
                "Reply with /menu for help."
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
                send_telegram_text(chat_id_str, "I couldn't find an answer. Please try rephrasing.\n\nReply with /menu for help.")
        else:
            send_telegram_text(chat_id_str, "Sorry, I encountered an error. Please try again.\n\nReply with /menu for help.")

        return jsonify({"ok": True, "answered": True})

    except Exception as e:
        logging.exception(f"TG webhook error: {e}")
        send_telegram_text(chat_id_str, "Sorry, I encountered an error. Please try again later.")
        return jsonify({"ok": True})
