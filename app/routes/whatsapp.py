# app/routes/whatsapp.py
from __future__ import annotations

import os
import re
import logging
from datetime import datetime, timedelta
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
from app.services.channel_subscription_service import (
    get_plans_list_menu,
    validate_plan_number,
    create_subscription_payment,
    format_subscription_message,
    get_user_email,
    request_email_message,
    has_active_subscription
)
from app.services.tax_filing_service import (
    save_filing_draft,
    delete_filing_draft,
    submit_tax_filing,
    get_user_filings
)
from app.services.tax_calculator import calculate_tax
from app.services.tax_deadline_service import get_upcoming_deadlines, get_deadlines_summary, format_deadline_message
from app.services.reminder_service import subscribe_to_reminders, unsubscribe_from_reminders, get_user_reminder_status
from app.services.language_service import t, get_user_language, set_user_language, get_language_menu, LANGUAGES

bp = Blueprint("whatsapp", __name__)

WA_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "").strip()

LINK_CODE_RE = re.compile(r"^[A-Z0-9]{8}$")
MENU_NUMBER_RE = re.compile(r"^[1-8]$")

user_states = {}


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
        res = supabase.rpc(
            "consume_link_token",
            {
                "p_provider": "wa",
                "p_code": code,
                "p_provider_user_id": provider_user_id,
            },
        ).execute()
    except Exception as e:
        return {"ok": False, "reason": "rpc_error", "error": str(e)}

    row = (res.data or [None])[0]
    if not row:
        return {"ok": False, "reason": "no_rpc_row"}

    if row.get("ok") is True and row.get("auth_user_id"):
        return {"ok": True, "auth_user_id": row.get("auth_user_id")}

    return {"ok": False, "reason": row.get("reason") or "consume_failed"}


def _send_main_menu(phone: str, lang: str = "en"):
    menu = f"*🤖 Naija Tax Guide*\n\n{t('main_menu', lang)}\n\n{t('global_commands', lang)}\n\n🌐 Reply LANGUAGE or L to change language"
    send_whatsapp_text(phone, menu)


def _send_tax_menu(phone: str, lang: str = "en"):
    menu = t('tax_menu', lang) + f"\n\n{t('global_commands', lang)}"
    send_whatsapp_text(phone, menu)


def _send_welcome(phone: str, lang: str = "en"):
    welcome = f"{t('welcome', lang)}\n\n{t('main_menu', lang)}\n\n{t('global_commands', lang)}\n\n🌐 Reply LANGUAGE or L to change language\n\n📅 Reply 'REMIND ME' for tax deadline alerts!"
    send_whatsapp_text(phone, welcome)


def _parse_amount(text: str) -> float:
    """Parse amount - handles decimals, N, ₦, commas, k, M suffix"""
    clean = text.replace(",", "").replace("₦", "").replace("N", "").replace("n", "").replace("naira", "").strip().lower()
    
    if clean.endswith("k"):
        clean = clean[:-1]
        return float(clean) * 1000
    
    if clean.endswith("m"):
        clean = clean[:-1]
        return float(clean) * 1000000
    
    if "million" in clean:
        clean = clean.replace("million", "").strip()
        return float(clean) * 1000000
    
    return float(clean)


def _get_active_filing(account_id: str):
    """Check database for any active filing"""
    try:
        result = supabase.table("tax_filing_drafts")\
            .select("*")\
            .eq("user_id", account_id)\
            .eq("status", "in_progress")\
            .limit(1)\
            .execute()
        
        if result.data and len(result.data) > 0:
            draft = result.data[0]
            return {
                "filing_type": draft.get("tax_type"),
                "step": draft.get("current_step", 1),
                "inputs": draft.get("inputs", {})
            }
    except Exception as e:
        logging.error(f"Failed to get active filing: {e}")
    
    return None


def _show_filing_step(phone: str, tax_type: str, step: int, inputs: dict, lang: str = "en"):
    if tax_type == "paye":
        if step == 1:
            send_whatsapp_text(phone, f"{t('paye_step1', lang)}\n\n{t('global_commands', lang)}")
        elif step == 2:
            send_whatsapp_text(phone, f"✅ Received: ₦{inputs.get('monthly_gross_income', 0):,.2f}\n\n{t('paye_step2', lang)}\n\n{t('global_commands', lang)}")
        elif step == 3:
            send_whatsapp_text(phone, f"✅ Received: ₦{inputs.get('pension_contribution', 0):,.2f}\n\n{t('paye_step3', lang)}\n\n{t('global_commands', lang)}")
    elif tax_type == "vat":
        if step == 1:
            send_whatsapp_text(phone, f"{t('vat_step1', lang)}\n\n{t('global_commands', lang)}")
        elif step == 2:
            send_whatsapp_text(phone, f"✅ Received: ₦{inputs.get('sales_amount', 0):,.2f}\n\n{t('vat_step2', lang)}\n\n{t('global_commands', lang)}")
    elif tax_type == "cit":
        if step == 1:
            send_whatsapp_text(phone, f"{t('cit_step1', lang)}\n\n{t('global_commands', lang)}")
        elif step == 2:
            send_whatsapp_text(phone, f"✅ Received: ₦{inputs.get('revenue', 0):,.2f}\n\n{t('cit_step2', lang)}\n\n{t('global_commands', lang)}")


def _handle_paye_filing(phone: str, account_id: str, step: int, inputs: dict, text: str, lang: str = "en"):
    if step == 1:
        try:
            amount = _parse_amount(text)
            inputs["monthly_gross_income"] = amount
            save_filing_draft(account_id, "paye", inputs, [], 2)
            user_states[phone] = {"context": "filing", "sub_context": "paye", "step": 2, "inputs": inputs, "lang": lang}
            _show_filing_step(phone, "paye", 2, inputs, lang)
            return True
        except ValueError:
            send_whatsapp_text(phone, f"{t('invalid_amount', lang)}\n\n{t('global_commands', lang)}")
            return True
    
    elif step == 2:
        try:
            amount = _parse_amount(text)
            inputs["pension_contribution"] = amount
            save_filing_draft(account_id, "paye", inputs, [], 3)
            user_states[phone] = {"context": "filing", "sub_context": "paye", "step": 3, "inputs": inputs, "lang": lang}
            _show_filing_step(phone, "paye", 3, inputs, lang)
            return True
        except ValueError:
            send_whatsapp_text(phone, f"{t('invalid_amount', lang)}\n\n{t('global_commands', lang)}")
            return True
    
    elif step == 3:
        try:
            amount = _parse_amount(text)
            inputs["nhf"] = amount
            save_filing_draft(account_id, "paye", inputs, [], 4)
            
            calc = calculate_tax("paye", inputs)
            monthly_tax = calc.get("monthly_tax_payable", 0)
            
            preview = (f"📋 *PAYE Filing Summary*\n\n"
                       f"• Monthly Salary: ₦{inputs.get('monthly_gross_income', 0):,.2f}\n"
                       f"• Pension: ₦{inputs.get('pension_contribution', 0):,.2f}\n"
                       f"• NHF: ₦{inputs.get('nhf', 0):,.2f}\n"
                       f"• *Monthly Tax: ₦{monthly_tax:,.2f}*\n\n"
                       f"Reply with 'confirm' to submit, or 'cancel' to abort")
            
            user_states[phone] = {"context": "filing_confirm", "sub_context": "paye", "step": 4, "inputs": inputs, "calculation": calc, "lang": lang}
            send_whatsapp_text(phone, preview)
            return True
        except ValueError:
            send_whatsapp_text(phone, f"{t('invalid_amount', lang)}\n\n{t('global_commands', lang)}")
            return True
    
    return False


def _handle_vat_filing(phone: str, account_id: str, step: int, inputs: dict, text: str, lang: str = "en"):
    if step == 1:
        try:
            amount = _parse_amount(text)
            inputs["sales_amount"] = amount
            save_filing_draft(account_id, "vat", inputs, [], 2)
            user_states[phone] = {"context": "filing", "sub_context": "vat", "step": 2, "inputs": inputs, "lang": lang}
            _show_filing_step(phone, "vat", 2, inputs, lang)
            return True
        except ValueError:
            send_whatsapp_text(phone, f"{t('invalid_amount', lang)}\n\n{t('global_commands', lang)}")
            return True
    
    elif step == 2:
        try:
            amount = _parse_amount(text)
            inputs["purchases_amount"] = amount
            save_filing_draft(account_id, "vat", inputs, [], 3)
            
            sales = inputs.get("sales_amount", 0)
            purchases = amount
            output_vat = sales * 0.075
            input_vat = purchases * 0.075
            vat_payable = max(0, output_vat - input_vat)
            
            preview = (f"📋 *VAT Filing Summary*\n\n"
                       f"• Total Sales: ₦{sales:,.2f}\n"
                       f"• Total Purchases: ₦{purchases:,.2f}\n"
                       f"• VAT Rate: 7.5%\n"
                       f"• *VAT Payable: ₦{vat_payable:,.2f}*\n\n"
                       f"Reply with 'confirm' to submit, or 'cancel' to abort")
            
            user_states[phone] = {"context": "filing_confirm", "sub_context": "vat", "step": 3, "inputs": inputs, "lang": lang}
            send_whatsapp_text(phone, preview)
            return True
        except ValueError:
            send_whatsapp_text(phone, f"{t('invalid_amount', lang)}\n\n{t('global_commands', lang)}")
            return True
    
    return False


def _handle_cit_filing(phone: str, account_id: str, step: int, inputs: dict, text: str, lang: str = "en"):
    if step == 1:
        try:
            amount = _parse_amount(text)
            inputs["revenue"] = amount
            save_filing_draft(account_id, "cit", inputs, [], 2)
            user_states[phone] = {"context": "filing", "sub_context": "cit", "step": 2, "inputs": inputs, "lang": lang}
            _show_filing_step(phone, "cit", 2, inputs, lang)
            return True
        except ValueError:
            send_whatsapp_text(phone, f"{t('invalid_amount', lang)}\n\n{t('global_commands', lang)}")
            return True
    
    elif step == 2:
        try:
            amount = _parse_amount(text)
            inputs["expenses"] = amount
            save_filing_draft(account_id, "cit", inputs, [], 3)
            
            revenue = inputs.get("revenue", 0)
            expenses = amount
            profit = max(0, revenue - expenses)
            
            if revenue > 100000000:
                applicable_rate = 30
            elif revenue > 25000000:
                applicable_rate = 20
            else:
                applicable_rate = 0
            
            cit_payable = profit * (applicable_rate / 100)
            company_size = "Large" if revenue > 100000000 else "Medium" if revenue > 25000000 else "Small"
            
            preview = (f"📋 *CIT Filing Summary*\n\n"
                       f"• Total Revenue: ₦{revenue:,.2f}\n"
                       f"• Total Expenses: ₦{expenses:,.2f}\n"
                       f"• Profit: ₦{profit:,.2f}\n"
                       f"• Company Size: {company_size}\n"
                       f"• Tax Rate: {applicable_rate}%\n"
                       f"• *CIT Payable: ₦{cit_payable:,.2f}*\n\n"
                       f"Reply with 'confirm' to submit, or 'cancel' to abort")
            
            user_states[phone] = {"context": "filing_confirm", "sub_context": "cit", "step": 3, "inputs": inputs, "lang": lang}
            send_whatsapp_text(phone, preview)
            return True
        except ValueError:
            send_whatsapp_text(phone, f"{t('invalid_amount', lang)}\n\n{t('global_commands', lang)}")
            return True
    
    return False


def _handle_submit(phone: str, account_id: str, user_state: dict):
    sub_context = user_state.get("sub_context")
    inputs = user_state.get("inputs", {})
    lang = user_state.get("lang", "en")
    
    if sub_context == "paye":
        result = submit_tax_filing(account_id, "paye", inputs, [])
        if result.get("ok"):
            calc = result.get("calculation", {})
            monthly_tax = calc.get("monthly_tax_payable", 0)
            reference = result.get("reference", "N/A")
            send_whatsapp_text(phone, t("filing_submitted", lang, tax_type="PAYE", reference=reference, tax_name="Monthly Tax", amount=monthly_tax))
        else:
            send_whatsapp_text(phone, f"❌ Filing failed: {result.get('error', 'Unknown error')}")
    elif sub_context == "vat":
        submission_inputs = {
            "taxable_supplies": inputs.get("sales_amount", 0),
            "input_vat": inputs.get("purchases_amount", 0) * 0.075,
        }
        result = submit_tax_filing(account_id, "vat", submission_inputs, [])
        if result.get("ok"):
            calc = result.get("calculation", {})
            vat_payable = calc.get("vat_payable", 0)
            reference = result.get("reference", "N/A")
            send_whatsapp_text(phone, t("filing_submitted", lang, tax_type="VAT", reference=reference, tax_name="VAT", amount=vat_payable))
        else:
            send_whatsapp_text(phone, f"❌ Filing failed: {result.get('error', 'Unknown error')}")
    elif sub_context == "cit":
        submission_inputs = {
            "gross_profit": inputs.get("revenue", 0) - inputs.get("expenses", 0),
            "allowable_expenses": inputs.get("expenses", 0),
        }
        result = submit_tax_filing(account_id, "cit", submission_inputs, [])
        if result.get("ok"):
            calc = result.get("calculation", {})
            cit_payable = calc.get("cit_payable", 0)
            reference = result.get("reference", "N/A")
            send_whatsapp_text(phone, t("filing_submitted", lang, tax_type="CIT", reference=reference, tax_name="CIT", amount=cit_payable))
        else:
            send_whatsapp_text(phone, f"❌ Filing failed: {result.get('error', 'Unknown error')}")
    
    delete_filing_draft(account_id, sub_context)
    user_states.pop(phone, None)


def _handle_filing_history(phone: str, account_id: str, lang: str = "en"):
    filings = get_user_filings(account_id, limit=10)
    if filings:
        msg = "📋 *Your Tax Filings*\n\n"
        for f in filings[:5]:
            status_emoji = "✅" if f.get('status') == 'submitted' else "⏳"
            msg += f"{status_emoji} *{f.get('tax_type', '').upper()}*: {f.get('reference', 'N/A')}\n"
            msg += f"   📅 {f.get('submitted_at', '')[:10]}\n\n"
        send_whatsapp_text(phone, msg)
    else:
        send_whatsapp_text(phone, "📋 No tax filings found. Reply with P to file PAYE tax, V for VAT, or C for CIT.")


def _handle_reminder_commands(phone: str, account_id: str, text: str, lang: str = "en") -> bool:
    text_lower = text.lower().strip()
    
    if text_lower == "r" or text_lower == "reminders":
        status = get_user_reminder_status(account_id, "whatsapp")
        if status.get("subscribed"):
            send_whatsapp_text(phone, "✅ *You are subscribed to reminders!*\n\nYou will receive tax deadline alerts.\n\nReply 'UNSUBSCRIBE' or 'U' to stop reminders.\n\nReply 'DEADLINES' to see upcoming deadlines.")
        else:
            send_whatsapp_text(phone, "🔔 *You are NOT subscribed to reminders*\n\nReply 'REMIND ME' to receive tax deadline alerts via WhatsApp!\n\nYou'll get reminders for:\n• PAYE (monthly)\n• VAT (monthly)\n• CIT (quarterly & annual)\n• And more!")
        return True
    
    if text_lower == "remind me" or text_lower == "remindme":
        result = subscribe_to_reminders(account_id, "whatsapp", phone)
        send_whatsapp_text(phone, result.get("message", "✅ You will now receive tax deadline reminders!"))
        return True
    
    if text_lower == "unsubscribe" or text_lower == "opt out" or text_lower == "u":
        result = unsubscribe_from_reminders(account_id, "whatsapp")
        send_whatsapp_text(phone, result.get("message", "❌ You have been unsubscribed from reminders."))
        return True
    
    if text_lower == "deadlines" or text_lower == "d":
        summary = get_deadlines_summary(30)
        send_whatsapp_text(phone, summary + "\n\n💡 Reply 'REMIND ME' to get alerts for these deadlines!")
        return True
    
    return False


def _handle_language_command(phone: str, account_id: str, text: str, current_lang: str) -> bool:
    text_upper = text.upper().strip()
    text_lower = text.lower().strip()
    
    # Check for language command
    if text_upper == "L" or text_lower == "language" or text_upper == "LANGUAGE":
        send_whatsapp_text(phone, get_language_menu())
        user_states[phone] = {"context": "changing_language", "lang": current_lang}
        return True
    
    return False


@bp.route("/whatsapp/webhook", methods=["GET", "POST"])
def wa_webhook():
    if request.method == "GET":
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")
        if mode == "subscribe" and token and WA_VERIFY_TOKEN and token == WA_VERIFY_TOKEN:
            return challenge, 200
        return "Forbidden", 403
    
    body = request.get_json(silent=True) or {}
    
    try:
        from_phone, text = _extract_message(body)
        if not from_phone:
            return jsonify({"ok": True, "ignored": True})
        
        upsert_account(provider="wa", provider_user_id=from_phone, display_name=None, phone=from_phone)
        lk = lookup_account(provider="wa", provider_user_id=from_phone)
        
        if not lk.get("ok"):
            send_whatsapp_text(from_phone, "System error. Please try again.")
            return jsonify({"ok": True})
        
        account_id = lk.get("account_id") or from_phone
        user_state = user_states.get(from_phone, {})
        
        # Get user's language preference
        user_lang = get_user_language(account_id)
        
        if not text:
            _send_welcome(from_phone, user_lang)
            return jsonify({"ok": True})
        
        # ============================================================
        # 1. Handle email collection for subscription
        # ============================================================
        if user_state.get("awaiting_email"):
            email = text.strip().lower()
            pending_plan = user_state.get("pending_plan")
            if email == "cancel" or email == "0":
                user_states.pop(from_phone, None)
                send_whatsapp_text(from_phone, "Subscription cancelled.")
                return jsonify({"ok": True})
            if "@" in email and "." in email:
                result = create_subscription_payment(account_id, pending_plan, "whatsapp", from_phone, email)
                if result.get("ok"):
                    send_whatsapp_text(from_phone, result["message"])
                else:
                    send_whatsapp_text(from_phone, f"❌ {result.get('message', 'Please try again.')}")
                user_states.pop(from_phone, None)
            else:
                send_whatsapp_text(from_phone, "❌ Invalid email. Send a valid email, 'cancel' to abort, or '#' to save and exit.\n\n💡 # - Save & Menu | 0 - Cancel")
            return jsonify({"ok": True})
        
        # ============================================================
        # 2. Handle language selection
        # ============================================================
        if user_state.get("context") == "changing_language":
            lang_code = text.strip().lower()
            if lang_code in LANGUAGES:
                result = set_user_language(account_id, lang_code)
                send_whatsapp_text(from_phone, result.get("message", f"Language changed to {LANGUAGES[lang_code]['name']}"))
                user_states.pop(from_phone, None)
                user_lang = lang_code
                _send_main_menu(from_phone, user_lang)
            else:
                send_whatsapp_text(from_phone, "❌ Invalid language code. Send EN, PCM, YO, IG, or HA.\n\n💡 Reply 8 to cancel")
            return jsonify({"ok": True})
        
        # ============================================================
        # 3. CRITICAL: Handle confirm/cancel IMMEDIATELY
        # ============================================================
        if text.lower() == "confirm":
            if user_state.get("context") == "filing_confirm":
                _handle_submit(from_phone, account_id, user_state)
            else:
                send_whatsapp_text(from_phone, t('no_filing', user_lang))
            return jsonify({"ok": True})
        
        if text.lower() == "cancel":
            if user_state.get("context") == "filing" or user_state.get("context") == "filing_confirm":
                delete_filing_draft(account_id, user_state.get("sub_context"))
                user_states.pop(from_phone, None)
                send_whatsapp_text(from_phone, "❌ Filing cancelled.\n\nReply 8 for main menu.")
            else:
                send_whatsapp_text(from_phone, "No active filing to cancel.")
            return jsonify({"ok": True})
        
        # ============================================================
        # 4. Language command
        # ============================================================
        if _handle_language_command(from_phone, account_id, text, user_lang):
            return jsonify({"ok": True})
        
        # ============================================================
        # 5. GLOBAL COMMANDS
        # ============================================================
        if text == "#":
            current_context = user_state.get("context")
            if current_context == "filing" or current_context == "filing_confirm":
                save_filing_draft(account_id, user_state.get("sub_context"), user_state.get("inputs", {}), [], user_state.get("step"))
                send_whatsapp_text(from_phone, "✅ Filing saved. You can resume later with 9.")
            elif current_context:
                send_whatsapp_text(from_phone, "✅ Progress saved.")
            else:
                send_whatsapp_text(from_phone, "ℹ️ Nothing to save.")
            _send_main_menu(from_phone, user_lang)
            user_states.pop(from_phone, None)
            return jsonify({"ok": True})
        
        if text == "0":
            current_context = user_state.get("context")
            if current_context == "filing" or current_context == "filing_confirm":
                delete_filing_draft(account_id, user_state.get("sub_context"))
            user_states.pop(from_phone, None)
            send_whatsapp_text(from_phone, "❌ Cancelled. All progress cleared.\n\nReply 8 for main menu.")
            return jsonify({"ok": True})
        
        if text == "9":
            active = _get_active_filing(account_id)
            if active:
                user_states[from_phone] = {
                    "context": "filing",
                    "sub_context": active["filing_type"],
                    "step": active["step"],
                    "inputs": active["inputs"],
                    "lang": user_lang
                }
                _show_filing_step(from_phone, active["filing_type"], active["step"], active["inputs"], user_lang)
            else:
                send_whatsapp_text(from_phone, "📭 No saved filing found. Start a new one with P, V, or C.")
            return jsonify({"ok": True})
        
        # Handle back command
        if text == "*":
            current_step = user_state.get("step")
            if current_step and current_step > 1:
                new_step = current_step - 1
                user_state["step"] = new_step
                user_states[from_phone] = user_state
                _show_filing_step(from_phone, user_state.get("sub_context"), new_step, user_state.get("inputs", {}), user_lang)
            else:
                _send_main_menu(from_phone, user_lang)
                user_states.pop(from_phone, None)
            return jsonify({"ok": True})
        
        # ============================================================
        # 6. CHECK FOR ACTIVE FILING
        # ============================================================
        filing_type = user_state.get("sub_context") if user_state.get("context") == "filing" else None
        step = user_state.get("step")
        inputs = user_state.get("inputs", {})
        
        if not filing_type:
            active = _get_active_filing(account_id)
            if active:
                filing_type = active["filing_type"]
                step = active["step"]
                inputs = active["inputs"]
                user_states[from_phone] = {
                    "context": "filing",
                    "sub_context": filing_type,
                    "step": step,
                    "inputs": inputs,
                    "lang": user_lang
                }
        
        if filing_type and step and step < 4:
            if filing_type == "paye":
                _handle_paye_filing(from_phone, account_id, step, inputs, text, user_lang)
            elif filing_type == "vat":
                _handle_vat_filing(from_phone, account_id, step, inputs, text, user_lang)
            elif filing_type == "cit":
                _handle_cit_filing(from_phone, account_id, step, inputs, text, user_lang)
            return jsonify({"ok": True})
        
        # ============================================================
        # 7. HANDLE REMINDER COMMANDS
        # ============================================================
        if _handle_reminder_commands(from_phone, account_id, text, user_lang):
            return jsonify({"ok": True})
        
        # ============================================================
        # 8. START NEW FILING
        # ============================================================
        text_lower = text.lower().strip()
        
        if text_lower in ["paye", "p"]:
            try:
                supabase.table("tax_filing_drafts").delete().eq("user_id", account_id).eq("tax_type", "paye").eq("status", "in_progress").execute()
            except:
                pass
            user_states[from_phone] = {"context": "filing", "sub_context": "paye", "step": 1, "inputs": {}, "lang": user_lang}
            send_whatsapp_text(from_phone, f"{t('paye_step1', user_lang)}\n\n{t('global_commands', user_lang)}")
            return jsonify({"ok": True})
        
        if text_lower in ["vat", "v"]:
            try:
                supabase.table("tax_filing_drafts").delete().eq("user_id", account_id).eq("tax_type", "vat").eq("status", "in_progress").execute()
            except:
                pass
            user_states[from_phone] = {"context": "filing", "sub_context": "vat", "step": 1, "inputs": {}, "lang": user_lang}
            send_whatsapp_text(from_phone, f"{t('vat_step1', user_lang)}\n\n{t('global_commands', user_lang)}")
            return jsonify({"ok": True})
        
        if text_lower in ["cit", "c"]:
            try:
                supabase.table("tax_filing_drafts").delete().eq("user_id", account_id).eq("tax_type", "cit").eq("status", "in_progress").execute()
            except:
                pass
            user_states[from_phone] = {"context": "filing", "sub_context": "cit", "step": 1, "inputs": {}, "lang": user_lang}
            send_whatsapp_text(from_phone, f"{t('cit_step1', user_lang)}\n\n{t('global_commands', user_lang)}")
            return jsonify({"ok": True})
        
        # ============================================================
        # 9. MENU COMMANDS
        # ============================================================
        if text.upper() == "H":
            _handle_filing_history(from_phone, account_id, user_lang)
            return jsonify({"ok": True})
        
        if text.upper() == "B":
            _send_main_menu(from_phone, user_lang)
            return jsonify({"ok": True})
        
        if text.upper() == "7" or text_lower == "tax":
            _send_tax_menu(from_phone, user_lang)
            return jsonify({"ok": True})
        
        if MENU_NUMBER_RE.match(text):
            option = int(text)
            if option == 7:
                _send_tax_menu(from_phone, user_lang)
            elif option == 8:
                _send_main_menu(from_phone, user_lang)
            elif option == 1:
                send_whatsapp_text(from_phone, "💬 Please type your tax question.\n\n💡 # - Save & Menu | 0 - Cancel")
            elif option == 2:
                if has_active_subscription(account_id):
                    send_whatsapp_text(from_phone, "💎 *UNLIMITED AI ACCESS* ✅\n\nYou have an active subscription. No credit limits!")
                else:
                    balance = get_credit_balance(account_id)
                    send_whatsapp_text(from_phone, format_balance_message(balance))
            elif option == 3:
                message = format_subscription_message(account_id)
                send_whatsapp_text(from_phone, message)
            elif option == 4:
                plans_menu = get_plans_list_menu()
                send_whatsapp_text(from_phone, plans_menu + "\n\n💡 Send a plan number 1-9 to subscribe, or # to save and exit.")
                user_states[from_phone] = {"context": "subscription"}
            elif option == 5:
                send_whatsapp_text(from_phone, "🔗 *Link to Website*\n\n1. Login to website\n2. Go to Settings → WhatsApp Linking\n3. Generate an 8-character code\n4. Send the code here\n\n💡 # - Save & Menu | 0 - Cancel")
                user_states[from_phone] = {"context": "linking", "awaiting_code": True}
            elif option == 6:
                if has_active_subscription(account_id):
                    send_whatsapp_text(from_phone, "✨ You have an active subscription with UNLIMITED credits!\n\nNo need to buy credits.")
                else:
                    credit_menu = get_credit_packages_menu()
                    send_whatsapp_text(from_phone, credit_menu + "\n\n💡 Send 1, 2, 3, or 4 to buy, or # to save and exit.")
                    user_states[from_phone] = {"context": "buying_credits"}
            return jsonify({"ok": True})
        
        # ============================================================
        # 10. CREDIT PACKAGE SELECTION
        # ============================================================
        if user_state.get("context") == "buying_credits" and text in ["1", "2", "3", "4"]:
            package_num = int(text)
            package = validate_package_number(package_num)
            if package:
                result = create_credit_payment(account_id, package_num, "whatsapp", from_phone)
                if result.get("ok"):
                    send_whatsapp_text(from_phone, result["message"])
                else:
                    send_whatsapp_text(from_phone, f"❌ {result.get('message', 'Please try again.')}")
            else:
                send_whatsapp_text(from_phone, "❌ Invalid package. Send 6 to see packages.")
            user_states.pop(from_phone, None)
            return jsonify({"ok": True})
        
        # ============================================================
        # 11. SUBSCRIPTION PLAN SELECTION
        # ============================================================
        if user_state.get("context") == "subscription" and text.isdigit() and 1 <= int(text) <= 9:
            plan_num = int(text)
            plan = validate_plan_number(plan_num)
            if plan:
                user_email = get_user_email(account_id)
                if user_email:
                    result = create_subscription_payment(account_id, plan, "whatsapp", from_phone, user_email)
                    if result.get("ok"):
                        send_whatsapp_text(from_phone, result["message"])
                    else:
                        send_whatsapp_text(from_phone, f"❌ {result.get('message', 'Please try again.')}")
                    user_states.pop(from_phone, None)
                else:
                    user_states[from_phone] = {"awaiting_email": True, "pending_plan": plan}
                    send_whatsapp_text(from_phone, request_email_message() + "\n\n💡 # - Save & Menu | 0 - Cancel")
            else:
                send_whatsapp_text(from_phone, "❌ Invalid plan number. Send 4 to see plans.")
            return jsonify({"ok": True})
        
        # ============================================================
        # 12. LINKING CODE
        # ============================================================
        if user_state.get("context") == "linking" and LINK_CODE_RE.match(text.upper()):
            attempt = _try_consume_link_code(from_phone, text)
            if attempt.get("ok"):
                send_whatsapp_text(from_phone, "✅ *WhatsApp linked successfully!*")
            else:
                send_whatsapp_text(from_phone, "❌ *Invalid link code*\n\nGenerate a new code on the website.")
            user_states.pop(from_phone, None)
            return jsonify({"ok": True})
        
        # ============================================================
        # 13. HELP
        # ============================================================
        if text.lower() in ["help", "menu", "start", "?", "/start", "8"]:
            _send_main_menu(from_phone, user_lang)
            return jsonify({"ok": True})
        
        # ============================================================
        # 14. DEFAULT: ASK AI (LAST RESORT)
        # ============================================================
        result = ask_guarded({"question": text, "account_id": account_id, "lang": user_lang, "channel": "whatsapp"})
        if result.get("ok"):
            answer = result.get("answer", "")
            if answer:
                send_whatsapp_text(from_phone, answer + "\n\n💡 Reply 8 for main menu.\n🌐 Reply LANGUAGE to change language")
            else:
                send_whatsapp_text(from_phone, "I couldn't find an answer. Reply 8 for menu.\n\n🌐 Reply LANGUAGE to change language")
        else:
            send_whatsapp_text(from_phone, "Sorry, I encountered an error. Reply 8 for menu.\n\n🌐 Reply LANGUAGE to change language")
        
        return jsonify({"ok": True})
        
    except Exception as e:
        logging.exception(f"WA webhook error: {e}")
        return jsonify({"ok": True})
