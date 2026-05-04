from __future__ import annotations

import os
import re
import logging
from datetime import datetime
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


def _send_main_menu(phone: str):
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
    send_whatsapp_text(phone, menu)


def _send_tax_menu(phone: str):
    menu = (
        "*📋 TAX FILING & MANAGEMENT*\n\n"
        "Reply with:\n"
        "P - File PAYE Tax (Salary tax)\n"
        "V - File VAT (Sales tax)\n"
        "C - File CIT (Company tax)\n"
        "H - View my filing history\n"
        "D - View tax deadlines\n"
        "B - Back to main menu\n\n"
        "Each filing takes 2-3 minutes.\n"
        "We'll guide you step by step!"
    )
    send_whatsapp_text(phone, menu)


def _send_welcome(phone: str):
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
    send_whatsapp_text(phone, welcome)


def _parse_amount(text: str) -> float:
    """Parse amount - handles N, ₦, commas, M suffix"""
    clean = text.replace(",", "").replace("₦", "").replace("N", "").replace("n", "").replace("naira", "").strip().lower()
    
    if "m" in clean:
        if "million" in clean:
            clean = clean.replace("million", "").strip()
        else:
            clean = clean.replace("m", "").strip()
        return float(clean) * 1000000
    
    return float(clean)


def _get_active_filing(account_id: str):
    """Check database for any active filing - returns (tax_type, step, inputs) or None"""
    try:
        # Query for any in_progress filing
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


def _handle_paye_filing(phone: str, account_id: str, step: int, inputs: dict, text: str):
    if step == 1:
        try:
            amount = _parse_amount(text)
            inputs["monthly_gross_income"] = amount
            save_filing_draft(account_id, "paye", inputs, [], 2)
            user_states[phone] = {"filing_type": "paye", "step": 2, "inputs": inputs}
            send_whatsapp_text(phone, f"✅ Received: ₦{amount:,.2f}\n\n📋 Step 2 of 3: Pension Contribution\nEnter your monthly pension contribution (usually 8% of salary, or 0 if none):")
            return True
        except ValueError:
            send_whatsapp_text(phone, "❌ Please enter a valid amount (e.g., 750000 or 750k)")
            return True
    
    elif step == 2:
        try:
            amount = _parse_amount(text)
            inputs["pension_contribution"] = amount
            save_filing_draft(account_id, "paye", inputs, [], 3)
            user_states[phone] = {"filing_type": "paye", "step": 3, "inputs": inputs}
            send_whatsapp_text(phone, f"✅ Received: ₦{amount:,.2f}\n\n📋 Step 3 of 3: NHF Contribution\nEnter your NHF contribution (if any, or 0):")
            return True
        except ValueError:
            send_whatsapp_text(phone, "❌ Please enter a valid amount")
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
            
            user_states[phone] = {"filing_type": "paye", "step": 4, "inputs": inputs}
            send_whatsapp_text(phone, preview)
            return True
        except ValueError:
            send_whatsapp_text(phone, "❌ Please enter a valid amount")
            return True
    
    elif step == 4:
        if text.lower() == "confirm":
            result = submit_tax_filing(account_id, "paye", inputs, [])
            if result.get("ok"):
                calc = result.get("calculation", {})
                monthly_tax = calc.get("monthly_tax_payable", 0)
                reference = result.get("reference", "N/A")
                
                send_whatsapp_text(phone, f"✅ *PAYE Filing Submitted!*\n\n📋 Reference: {reference}\n💰 Monthly Tax: ₦{monthly_tax:,.2f}\n\nReply with H to see all filings.")
                user_states.pop(phone, None)
                delete_filing_draft(account_id, "paye")
            else:
                send_whatsapp_text(phone, f"❌ Filing failed: {result.get('error', 'Unknown error')}")
        elif text.lower() == "cancel":
            delete_filing_draft(account_id, "paye")
            user_states.pop(phone, None)
            send_whatsapp_text(phone, "❌ Filing cancelled.")
        else:
            send_whatsapp_text(phone, "Reply with 'confirm' to submit or 'cancel' to abort")
        return True
    
    return False


def _handle_vat_filing(phone: str, account_id: str, step: int, inputs: dict, text: str):
    if step == 1:
        try:
            amount = _parse_amount(text)
            inputs["sales_amount"] = amount
            save_filing_draft(account_id, "vat", inputs, [], 2)
            user_states[phone] = {"filing_type": "vat", "step": 2, "inputs": inputs}
            send_whatsapp_text(phone, f"✅ Received: ₦{amount:,.2f}\n\n📋 Step 2 of 3: Total Purchases\nEnter your total purchases (excluding VAT):")
            return True
        except ValueError:
            send_whatsapp_text(phone, "❌ Please enter a valid amount")
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
            
            user_states[phone] = {"filing_type": "vat", "step": 3, "inputs": inputs}
            send_whatsapp_text(phone, preview)
            return True
        except ValueError:
            send_whatsapp_text(phone, "❌ Please enter a valid amount")
            return True
    
    elif step == 3:
        if text.lower() == "confirm":
            submission_inputs = {
                "taxable_supplies": inputs.get("sales_amount", 0),
                "input_vat": inputs.get("purchases_amount", 0) * 0.075,
            }
            result = submit_tax_filing(account_id, "vat", submission_inputs, [])
            if result.get("ok"):
                calc = result.get("calculation", {})
                vat_payable = calc.get("vat_payable", 0)
                reference = result.get("reference", "N/A")
                
                send_whatsapp_text(phone, f"✅ *VAT Filing Submitted!*\n\n📋 Reference: {reference}\n💰 VAT Payable: ₦{vat_payable:,.2f}")
                user_states.pop(phone, None)
                delete_filing_draft(account_id, "vat")
            else:
                send_whatsapp_text(phone, f"❌ Filing failed: {result.get('error', 'Unknown error')}")
        elif text.lower() == "cancel":
            delete_filing_draft(account_id, "vat")
            user_states.pop(phone, None)
            send_whatsapp_text(phone, "❌ Filing cancelled.")
        else:
            send_whatsapp_text(phone, "Reply with 'confirm' to submit or 'cancel' to abort")
        return True
    
    return False


def _handle_cit_filing(phone: str, account_id: str, step: int, inputs: dict, text: str):
    if step == 1:
        try:
            amount = _parse_amount(text)
            inputs["revenue"] = amount
            save_filing_draft(account_id, "cit", inputs, [], 2)
            user_states[phone] = {"filing_type": "cit", "step": 2, "inputs": inputs}
            send_whatsapp_text(phone, f"✅ Received: ₦{amount:,.2f}\n\n📋 Step 2 of 3: Total Expenses\nEnter your total allowable expenses:")
            return True
        except ValueError:
            send_whatsapp_text(phone, "❌ Please enter a valid amount (e.g., 25000000 or 25M)")
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
            
            user_states[phone] = {"filing_type": "cit", "step": 3, "inputs": inputs}
            send_whatsapp_text(phone, preview)
            return True
        except ValueError:
            send_whatsapp_text(phone, "❌ Please enter a valid amount")
            return True
    
    elif step == 3:
        if text.lower() == "confirm":
            submission_inputs = {
                "gross_profit": inputs.get("revenue", 0) - inputs.get("expenses", 0),
                "allowable_expenses": inputs.get("expenses", 0),
            }
            result = submit_tax_filing(account_id, "cit", submission_inputs, [])
            if result.get("ok"):
                calc = result.get("calculation", {})
                cit_payable = calc.get("cit_payable", 0)
                reference = result.get("reference", "N/A")
                
                send_whatsapp_text(phone, f"✅ *CIT Filing Submitted!*\n\n📋 Reference: {reference}\n💰 CIT Payable: ₦{cit_payable:,.2f}")
                user_states.pop(phone, None)
                delete_filing_draft(account_id, "cit")
            else:
                send_whatsapp_text(phone, f"❌ Filing failed: {result.get('error', 'Unknown error')}")
        elif text.lower() == "cancel":
            delete_filing_draft(account_id, "cit")
            user_states.pop(phone, None)
            send_whatsapp_text(phone, "❌ Filing cancelled.")
        else:
            send_whatsapp_text(phone, "Reply with 'confirm' to submit or 'cancel' to abort")
        return True
    
    return False


def _handle_filing_history(phone: str, account_id: str):
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
        
        if not text:
            _send_welcome(from_phone)
            return jsonify({"ok": True})
        
        # Handle email collection for subscription
        if user_state.get("awaiting_email"):
            email = text.strip().lower()
            pending_plan = user_state.get("pending_plan")
            if email == "cancel":
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
                send_whatsapp_text(from_phone, "❌ Invalid email. Send a valid email or 'cancel' to abort.")
            return jsonify({"ok": True})
        
        # ========== CRITICAL: Check for active filing ==========
        # Check memory first
        filing_type = user_state.get("filing_type")
        step = user_state.get("step")
        inputs = user_state.get("inputs", {})
        
        # If not in memory, check database
        if not filing_type:
            active = _get_active_filing(account_id)
            if active:
                filing_type = active["filing_type"]
                step = active["step"]
                inputs = active["inputs"]
                user_states[from_phone] = {"filing_type": filing_type, "step": step, "inputs": inputs}
                logging.info(f"Restored filing from DB: {filing_type} step {step}")
        
        # Process active filing
        if filing_type and step:
            if filing_type == "paye":
                _handle_paye_filing(from_phone, account_id, step, inputs, text)
            elif filing_type == "vat":
                _handle_vat_filing(from_phone, account_id, step, inputs, text)
            elif filing_type == "cit":
                _handle_cit_filing(from_phone, account_id, step, inputs, text)
            return jsonify({"ok": True})
        
        # ========== START NEW FILING ==========
        text_lower = text.lower().strip()
        
        if text_lower in ["paye", "p"]:
            user_states[from_phone] = {"filing_type": "paye", "step": 1, "inputs": {}}
            send_whatsapp_text(from_phone, "📋 *PAYE Tax Filing - Step 1 of 3*\n\nWhat is your monthly salary?\n(Example: 750000 or 750k)")
            return jsonify({"ok": True})
        
        if text_lower in ["vat", "v"]:
            user_states[from_phone] = {"filing_type": "vat", "step": 1, "inputs": {}}
            send_whatsapp_text(from_phone, "📋 *VAT Filing - Step 1 of 3*\n\nWhat is your total sales for the period?\n(Example: 25000000 or 25M)")
            return jsonify({"ok": True})
        
        if text_lower in ["cit", "c"]:
            user_states[from_phone] = {"filing_type": "cit", "step": 1, "inputs": {}}
            send_whatsapp_text(from_phone, "📋 *CIT Filing - Step 1 of 3*\n\nWhat is your company's total revenue for the period?\n(Example: 50000000 or 50M)")
            return jsonify({"ok": True})
        
        # Handle menu commands
        if text.upper() == "H":
            _handle_filing_history(from_phone, account_id)
            return jsonify({"ok": True})
        
        if text.upper() == "D":
            send_whatsapp_text(from_phone, "📅 *Tax Deadlines*\n\n• PAYE: Monthly by 10th\n• VAT: Monthly by 21st\n• CIT: 6 months after year end")
            return jsonify({"ok": True})
        
        if text.upper() == "B":
            _send_main_menu(from_phone)
            return jsonify({"ok": True})
        
        if text.upper() == "7" or text_lower == "tax":
            _send_tax_menu(from_phone)
            return jsonify({"ok": True})
        
        if MENU_NUMBER_RE.match(text):
            option = int(text)
            if option == 7:
                _send_tax_menu(from_phone)
                return jsonify({"ok": True})
            elif option == 8:
                _send_main_menu(from_phone)
                return jsonify({"ok": True})
            elif option == 1:
                send_whatsapp_text(from_phone, "💬 Please type your tax question.")
                return jsonify({"ok": True})
            elif option == 5:
                send_whatsapp_text(from_phone, "🔗 *Link to Website*\n\n1. Login to website\n2. Go to Settings → WhatsApp Linking\n3. Generate an 8-character code\n4. Send the code here")
                return jsonify({"ok": True})
        
        # Handle link codes (only if not in filing)
        if LINK_CODE_RE.match(text.upper()):
            attempt = _try_consume_link_code(from_phone, text)
            if attempt.get("ok"):
                send_whatsapp_text(from_phone, "✅ *WhatsApp linked successfully!*")
            else:
                send_whatsapp_text(from_phone, "❌ *Invalid link code*\n\nGenerate a new code on the website.")
            return jsonify({"ok": True})
        
        # Default: Ask AI
        result = ask_guarded({"question": text, "account_id": account_id, "lang": "en", "channel": "whatsapp"})
        if result.get("ok"):
            answer = result.get("answer", "")
            if answer:
                send_whatsapp_text(from_phone, answer)
            else:
                send_whatsapp_text(from_phone, "I couldn't find an answer. Reply 8 for menu.")
        else:
            send_whatsapp_text(from_phone, "Sorry, I encountered an error. Reply 8 for menu.")
        
        return jsonify({"ok": True})
        
    except Exception as e:
        logging.exception(f"WA webhook error: {e}")
        return jsonify({"ok": True})
