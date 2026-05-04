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

bp = Blueprint("whatsapp", __name__)

WA_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "").strip()

LINK_CODE_RE = re.compile(r"^[A-Z0-9]{8}$")
MENU_NUMBER_RE = re.compile(r"^[1-8]$")
AMOUNT_RE = re.compile(r"^[\d\.,]+(k|m|million)?$", re.IGNORECASE)

user_states = {}
SAVED_STATE_TIMEOUT_HOURS = 24


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
        "💡 Global commands (anytime):\n"
        "# - Save & Menu\n"
        "* - Back\n"
        "0 - Cancel\n"
        "9 - Resume"
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
        "We'll guide you step by step!\n\n"
        "💡 Global commands:\n"
        "# - Save & Menu | * - Back | 0 - Cancel | 9 - Resume"
    )
    send_whatsapp_text(phone, menu)


def _send_help(phone: str):
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
        "• *Global commands* (anytime):\n"
        "  # - Save progress and return to menu\n"
        "  * - Go back one step\n"
        "  0 - Cancel current action\n"
        "  9 - Resume saved activity\n\n"
        "Need help? Email support@naijataxguides.com"
    )
    send_whatsapp_text(phone, help_msg)


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
        "💡 Global commands (anytime):\n"
        "# - Save & Menu | * - Back | 0 - Cancel | 9 - Resume\n\n"
        "Or just type your tax question!"
    )
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


def _save_state_to_db(account_id: str, context: str, data: dict, step: int = None):
    """Save user state to database for later resume"""
    try:
        expires_at = (datetime.utcnow() + timedelta(hours=SAVED_STATE_TIMEOUT_HOURS)).isoformat()
        
        # Check if existing saved state
        existing = supabase.table("user_saved_states")\
            .select("id")\
            .eq("user_id", account_id)\
            .eq("context", context)\
            .maybe_single()\
            .execute()
        
        if existing.data:
            supabase.table("user_saved_states")\
                .update({
                    "data": data,
                    "step": step,
                    "expires_at": expires_at,
                    "updated_at": datetime.utcnow().isoformat()
                })\
                .eq("id", existing.data["id"])\
                .execute()
        else:
            supabase.table("user_saved_states").insert({
                "user_id": account_id,
                "context": context,
                "data": data,
                "step": step,
                "expires_at": expires_at
            }).execute()
    except Exception as e:
        logging.error(f"Failed to save state to DB: {e}")


def _load_state_from_db(account_id: str, context: str = None):
    """Load saved user state from database"""
    try:
        query = supabase.table("user_saved_states")\
            .select("*")\
            .eq("user_id", account_id)\
            .gte("expires_at", datetime.utcnow().isoformat())
        
        if context:
            query = query.eq("context", context)
        
        result = query.order("updated_at", desc=True).limit(1).execute()
        
        if result.data and len(result.data) > 0:
            return result.data[0]
    except Exception as e:
        logging.error(f"Failed to load state from DB: {e}")
    
    return None


def _delete_state_from_db(account_id: str, context: str = None):
    """Delete saved user state from database"""
    try:
        query = supabase.table("user_saved_states").delete().eq("user_id", account_id)
        if context:
            query = query.eq("context", context)
        query.execute()
    except Exception as e:
        logging.error(f"Failed to delete state from DB: {e}")


def _get_active_filing(account_id: str):
    """Check database for any active filing - returns (tax_type, step, inputs) or None"""
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


def _show_filing_step(phone: str, tax_type: str, step: int, inputs: dict):
    """Show the appropriate prompt for a filing step"""
    if tax_type == "paye":
        if step == 1:
            send_whatsapp_text(phone, "📋 *PAYE Tax Filing - Step 1 of 3*\n\nWhat is your monthly salary?\n(Example: 750000 or 750k)\n\n💡 * - Back | # - Save & Menu | 0 - Cancel")
        elif step == 2:
            send_whatsapp_text(phone, f"✅ Received: ₦{inputs.get('monthly_gross_income', 0):,.2f}\n\n📋 Step 2 of 3: Pension Contribution\nEnter your monthly pension contribution (usually 8% of salary, or 0 if none):\n\n💡 * - Back | # - Save & Menu | 0 - Cancel")
        elif step == 3:
            send_whatsapp_text(phone, f"✅ Received: ₦{inputs.get('pension_contribution', 0):,.2f}\n\n📋 Step 3 of 3: NHF Contribution\nEnter your NHF contribution (if any, or 0):\n\n💡 * - Back | # - Save & Menu | 0 - Cancel")
    elif tax_type == "vat":
        if step == 1:
            send_whatsapp_text(phone, "📋 *VAT Filing - Step 1 of 3*\n\nWhat is your total sales for the period?\n(Example: 25000000 or 25M)\n\n💡 * - Back | # - Save & Menu | 0 - Cancel")
        elif step == 2:
            send_whatsapp_text(phone, f"✅ Received: ₦{inputs.get('sales_amount', 0):,.2f}\n\n📋 Step 2 of 3: Total Purchases\nEnter your total purchases (excluding VAT):\n\n💡 * - Back | # - Save & Menu | 0 - Cancel")
    elif tax_type == "cit":
        if step == 1:
            send_whatsapp_text(phone, "📋 *CIT Filing - Step 1 of 3*\n\nWhat is your company's total revenue for the period?\n(Example: 50000000 or 50M)\n\n💡 * - Back | # - Save & Menu | 0 - Cancel")
        elif step == 2:
            send_whatsapp_text(phone, f"✅ Received: ₦{inputs.get('revenue', 0):,.2f}\n\n📋 Step 2 of 3: Total Expenses\nEnter your total allowable expenses:\n\n💡 * - Back | # - Save & Menu | 0 - Cancel")


def _handle_paye_filing(phone: str, account_id: str, step: int, inputs: dict, text: str):
    """Handle PAYE filing steps"""
    # Handle back command
    if text == "*" and step > 1:
        new_step = step - 1
        user_states[phone] = {"context": "filing", "sub_context": "paye", "step": new_step, "inputs": inputs}
        _show_filing_step(phone, "paye", new_step, inputs)
        return True
    
    if step == 1:
        try:
            amount = _parse_amount(text)
            inputs["monthly_gross_income"] = amount
            save_filing_draft(account_id, "paye", inputs, [], 2)
            user_states[phone] = {"context": "filing", "sub_context": "paye", "step": 2, "inputs": inputs}
            _show_filing_step(phone, "paye", 2, inputs)
            return True
        except ValueError:
            send_whatsapp_text(phone, "❌ Please enter a valid amount (e.g., 750000 or 750k)\n\n💡 * - Back | # - Save & Menu | 0 - Cancel")
            return True
    
    elif step == 2:
        try:
            amount = _parse_amount(text)
            inputs["pension_contribution"] = amount
            save_filing_draft(account_id, "paye", inputs, [], 3)
            user_states[phone] = {"context": "filing", "sub_context": "paye", "step": 3, "inputs": inputs}
            _show_filing_step(phone, "paye", 3, inputs)
            return True
        except ValueError:
            send_whatsapp_text(phone, "❌ Please enter a valid amount\n\n💡 * - Back | # - Save & Menu | 0 - Cancel")
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
                       f"Reply with 'confirm' to submit, or 'cancel' to abort\n\n"
                       f"💡 # - Save & Menu | 0 - Cancel")
            
            user_states[phone] = {"context": "filing_confirm", "sub_context": "paye", "step": 4, "inputs": inputs, "calculation": calc}
            send_whatsapp_text(phone, preview)
            return True
        except ValueError:
            send_whatsapp_text(phone, "❌ Please enter a valid amount\n\n💡 * - Back | # - Save & Menu | 0 - Cancel")
            return True
    
    elif step == 4:
        if text.lower() == "confirm":
            result = submit_tax_filing(account_id, "paye", inputs, [])
            if result.get("ok"):
                calc = result.get("calculation", {})
                monthly_tax = calc.get("monthly_tax_payable", 0)
                reference = result.get("reference", "N/A")
                
                send_whatsapp_text(phone, f"✅ *PAYE Filing Submitted!*\n\n📋 Reference: {reference}\n💰 Monthly Tax: ₦{monthly_tax:,.2f}\n\nReply with H to see all filings, or 8 for main menu.")
                user_states.pop(phone, None)
                delete_filing_draft(account_id, "paye")
            else:
                send_whatsapp_text(phone, f"❌ Filing failed: {result.get('error', 'Unknown error')}")
        elif text.lower() == "cancel":
            delete_filing_draft(account_id, "paye")
            user_states.pop(phone, None)
            send_whatsapp_text(phone, "❌ Filing cancelled.\n\nReply 8 for main menu.")
        else:
            send_whatsapp_text(phone, "Reply with 'confirm' to submit or 'cancel' to abort\n\n💡 # - Save & Menu | 0 - Cancel")
        return True
    
    return False


def _handle_vat_filing(phone: str, account_id: str, step: int, inputs: dict, text: str):
    """Handle VAT filing steps"""
    if text == "*" and step > 1:
        new_step = step - 1
        user_states[phone] = {"context": "filing", "sub_context": "vat", "step": new_step, "inputs": inputs}
        _show_filing_step(phone, "vat", new_step, inputs)
        return True
    
    if step == 1:
        try:
            amount = _parse_amount(text)
            inputs["sales_amount"] = amount
            save_filing_draft(account_id, "vat", inputs, [], 2)
            user_states[phone] = {"context": "filing", "sub_context": "vat", "step": 2, "inputs": inputs}
            _show_filing_step(phone, "vat", 2, inputs)
            return True
        except ValueError:
            send_whatsapp_text(phone, "❌ Please enter a valid amount (e.g., 25000000 or 25M)\n\n💡 * - Back | # - Save & Menu | 0 - Cancel")
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
                       f"Reply with 'confirm' to submit, or 'cancel' to abort\n\n"
                       f"💡 # - Save & Menu | 0 - Cancel")
            
            user_states[phone] = {"context": "filing_confirm", "sub_context": "vat", "step": 3, "inputs": inputs}
            send_whatsapp_text(phone, preview)
            return True
        except ValueError:
            send_whatsapp_text(phone, "❌ Please enter a valid amount\n\n💡 * - Back | # - Save & Menu | 0 - Cancel")
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
            send_whatsapp_text(phone, "❌ Filing cancelled.\n\nReply 8 for main menu.")
        else:
            send_whatsapp_text(phone, "Reply with 'confirm' to submit or 'cancel' to abort\n\n💡 # - Save & Menu | 0 - Cancel")
        return True
    
    return False


def _handle_cit_filing(phone: str, account_id: str, step: int, inputs: dict, text: str):
    """Handle CIT filing steps"""
    if text == "*" and step > 1:
        new_step = step - 1
        user_states[phone] = {"context": "filing", "sub_context": "cit", "step": new_step, "inputs": inputs}
        _show_filing_step(phone, "cit", new_step, inputs)
        return True
    
    if step == 1:
        try:
            amount = _parse_amount(text)
            inputs["revenue"] = amount
            save_filing_draft(account_id, "cit", inputs, [], 2)
            user_states[phone] = {"context": "filing", "sub_context": "cit", "step": 2, "inputs": inputs}
            _show_filing_step(phone, "cit", 2, inputs)
            return True
        except ValueError:
            send_whatsapp_text(phone, "❌ Please enter a valid amount (e.g., 25000000 or 25M)\n\n💡 * - Back | # - Save & Menu | 0 - Cancel")
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
                       f"Reply with 'confirm' to submit, or 'cancel' to abort\n\n"
                       f"💡 # - Save & Menu | 0 - Cancel")
            
            user_states[phone] = {"context": "filing_confirm", "sub_context": "cit", "step": 3, "inputs": inputs}
            send_whatsapp_text(phone, preview)
            return True
        except ValueError:
            send_whatsapp_text(phone, "❌ Please enter a valid amount\n\n💡 * - Back | # - Save & Menu | 0 - Cancel")
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
            send_whatsapp_text(phone, "❌ Filing cancelled.\n\nReply 8 for main menu.")
        else:
            send_whatsapp_text(phone, "Reply with 'confirm' to submit or 'cancel' to abort\n\n💡 # - Save & Menu | 0 - Cancel")
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


def _handle_resume(phone: str, account_id: str):
    """Resume saved activity"""
    # Check for saved filing first
    active = _get_active_filing(account_id)
    if active:
        user_states[phone] = {
            "context": "filing",
            "sub_context": active["filing_type"],
            "step": active["step"],
            "inputs": active["inputs"]
        }
        _show_filing_step(phone, active["filing_type"], active["step"], active["inputs"])
        return True
    
    # Check for other saved states
    saved = _load_state_from_db(account_id)
    if saved:
        context = saved.get("context")
        if context == "link_code":
            send_whatsapp_text(phone, "🔗 Resuming account linking. Send your 8-character code from the website.\n\n💡 # - Save & Menu | 0 - Cancel")
            user_states[phone] = {"context": "linking", "awaiting_code": True}
        elif context == "credit_purchase":
            send_whatsapp_text(phone, "💳 Resuming credit purchase. Send 1, 2, 3, or 4 to select a package.\n\n💡 # - Save & Menu | 0 - Cancel")
            user_states[phone] = {"context": "buying_credits"}
        elif context == "subscription":
            send_whatsapp_text(phone, "📋 Resuming subscription selection. Send a plan number 1-9.\n\n💡 # - Save & Menu | 0 - Cancel")
            user_states[phone] = {"context": "subscription"}
        else:
            send_whatsapp_text(phone, "✅ No saved activity found. Reply 8 for main menu.")
        return True
    
    send_whatsapp_text(phone, "📭 No saved filing found. Start a new one with P, V, or C.\n\nReply 8 for main menu.")
    return True


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
        
        # ========== GLOBAL COMMANDS (work everywhere) ==========
        
        # # - Save & Menu
        if text == "#":
            current_context = user_state.get("context")
            
            if current_context == "filing":
                # Save filing to DB
                save_filing_draft(
                    account_id,
                    user_state.get("sub_context"),
                    user_state.get("inputs", {}),
                    [],
                    user_state.get("step")
                )
                _save_state_to_db(account_id, "filing", user_state.get("inputs", {}), user_state.get("step"))
                send_whatsapp_text(from_phone, "✅ Filing saved. You can resume later with 9.")
            elif current_context:
                _save_state_to_db(account_id, current_context, user_state)
                send_whatsapp_text(from_phone, "✅ Progress saved. You can resume later with 9.")
            else:
                send_whatsapp_text(from_phone, "ℹ️ Nothing to save.")
            
            _send_main_menu(from_phone)
            user_states.pop(from_phone, None)
            return jsonify({"ok": True})
        
        # 0 - Cancel (clear all state)
        if text == "0":
            current_context = user_state.get("context")
            if current_context == "filing":
                delete_filing_draft(account_id, user_state.get("sub_context"))
            _delete_state_from_db(account_id)
            user_states.pop(from_phone, None)
            send_whatsapp_text(from_phone, "❌ Cancelled. All progress cleared.\n\nReply 8 for main menu.")
            return jsonify({"ok": True})
        
        # 9 - Resume
        if text == "9":
            _handle_resume(from_phone, account_id)
            return jsonify({"ok": True})
        
        # Handle email collection for subscription
        if user_state.get("awaiting_email"):
            email = text.strip().lower()
            pending_plan = user_state.get("pending_plan")
            
            if email == "cancel" or email == "0":
                user_states.pop(from_phone, None)
                send_whatsapp_text(from_phone, "❌ Subscription cancelled. Reply 8 for main menu.")
                return jsonify({"ok": True})
            
            if text == "#":
                _save_state_to_db(account_id, "subscription_email", {"plan": pending_plan, "email": None})
                send_whatsapp_text(from_phone, "✅ Progress saved. You can resume later with 9.")
                _send_main_menu(from_phone)
                user_states.pop(from_phone, None)
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
        
        # ========== CHECK FOR ACTIVE FILING ==========
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
                    "inputs": inputs
                }
        
        # Process active filing
        if filing_type and step and step < 4:
            if filing_type == "paye":
                _handle_paye_filing(from_phone, account_id, step, inputs, text)
            elif filing_type == "vat":
                _handle_vat_filing(from_phone, account_id, step, inputs, text)
            elif filing_type == "cit":
                _handle_cit_filing(from_phone, account_id, step, inputs, text)
            return jsonify({"ok": True})
        
        # ========== START NEW FILING ==========
        text_lower = text.lower().strip()
        
        # Check for existing saved filing before starting new        def check_and_start_new(tax_type: str, prompt: str):
            existing = _get_active_filing(account_id)
            if existing and existing["filing_type"] == tax_type:
                send_whatsapp_text(from_phone, f"📋 You have an unfinished {tax_type.upper()} filing.\n\nReply RESUME to continue, or NEW to start over.\n\n💡 # - Save & Menu | 0 - Cancel")
                user_states[from_phone] = {"context": "conflict", "pending_tax_type": tax_type, "pending_prompt": prompt}
                return True
            return False
        
        if text_lower in ["paye", "p"]:
            if check_and_start_new("paye", "📋 *PAYE Tax Filing - Step 1 of 3*\n\nWhat is your monthly salary?\n(Example: 750000 or 750k)"):
                return jsonify({"ok": True})
            user_states[from_phone] = {"context": "filing", "sub_context": "paye", "step": 1, "inputs": {}}
            send_whatsapp_text(from_phone, "📋 *PAYE Tax Filing - Step 1 of 3*\n\nWhat is your monthly salary?\n(Example: 750000 or 750k)\n\n💡 * - Back | # - Save & Menu | 0 - Cancel")
            return jsonify({"ok": True})
        
        if text_lower in ["vat", "v"]:
            if check_and_start_new("vat", "📋 *VAT Filing - Step 1 of 3*\n\nWhat is your total sales for the period?\n(Example: 25000000 or 25M)"):
                return jsonify({"ok": True})
            user_states[from_phone] = {"context": "filing", "sub_context": "vat", "step": 1, "inputs": {}}
            send_whatsapp_text(from_phone, "📋 *VAT Filing - Step 1 of 3*\n\nWhat is your total sales for the period?\n(Example: 25000000 or 25M)\n\n💡 * - Back | # - Save & Menu | 0 - Cancel")
            return jsonify({"ok": True})
        
        if text_lower in ["cit", "c"]:
            if check_and_start_new("cit", "📋 *CIT Filing - Step 1 of 3*\n\nWhat is your company's total revenue for the period?\n(Example: 50000000 or 50M)"):
                return jsonify({"ok": True})
            user_states[from_phone] = {"context": "filing", "sub_context": "cit", "step": 1, "inputs": {}}
            send_whatsapp_text(from_phone, "📋 *CIT Filing - Step 1 of 3*\n\nWhat is your company's total revenue for the period?\n(Example: 50000000 or 50M)\n\n💡 * - Back | # - Save & Menu | 0 - Cancel")
            return jsonify({"ok": True})
        
        # Handle conflict resolution (RESUME / NEW)
        if user_state.get("context") == "conflict":
            if text.upper() == "RESUME":
                active = _get_active_filing(account_id)
                if active:
                    user_states[from_phone] = {
                        "context": "filing",
                        "sub_context": active["filing_type"],
                        "step": active["step"],
                        "inputs": active["inputs"]
                    }
                    _show_filing_step(from_phone, active["filing_type"], active["step"], active["inputs"])
                else:
                    send_whatsapp_text(from_phone, "No saved filing found. Starting new one.")
                    send_whatsapp_text(from_phone, user_state.get("pending_prompt", ""))
                    user_states[from_phone] = {"context": "filing", "sub_context": user_state.get("pending_tax_type"), "step": 1, "inputs": {}}
            elif text.upper() == "NEW":
                delete_filing_draft(account_id, user_state.get("pending_tax_type"))
                user_states[from_phone] = {"context": "filing", "sub_context": user_state.get("pending_tax_type"), "step": 1, "inputs": {}}
                send_whatsapp_text(from_phone, user_state.get("pending_prompt", ""))
            else:
                send_whatsapp_text(from_phone, "Reply RESUME to continue your filing, or NEW to start over.\n\n💡 # - Save & Menu | 0 - Cancel")
            return jsonify({"ok": True})
        
        # ========== MENU COMMANDS ==========
        if text.upper() == "H":
            _handle_filing_history(from_phone, account_id)
            return jsonify({"ok": True})
        
        if text.upper() == "D":
            send_whatsapp_text(from_phone, "📅 *Tax Deadlines*\n\n• PAYE: Monthly by 10th\n• VAT: Monthly by 21st\n• CIT: 6 months after year end\n• Annual Returns: March 31st\n\n💡 # - Save & Menu | 0 - Cancel")
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
            elif option == 8:
                _send_main_menu(from_phone)
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
        
        # Handle credit package selection
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
        
        # Handle subscription plan selection
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
        
        # Handle linking code
        if user_state.get("context") == "linking" and LINK_CODE_RE.match(text.upper()):
            attempt = _try_consume_link_code(from_phone, text)
            if attempt.get("ok"):
                send_whatsapp_text(from_phone, "✅ *WhatsApp linked successfully!*")
            else:
                send_whatsapp_text(from_phone, "❌ *Invalid link code*\n\nGenerate a new code on the website.\n\n💡 # - Save & Menu | 0 - Cancel")
            user_states.pop(from_phone, None)
            return jsonify({"ok": True})
        
        # Handle help
        if text.lower() in ["help", "menu", "start", "?", "/start", "8"]:
            _send_main_menu(from_phone)
            return jsonify({"ok": True})
        
        # Handle back command (*) in non-filing contexts
        if text == "*":
            if user_state.get("context"):
                user_states.pop(from_phone, None)
                send_whatsapp_text(from_phone, "↩️ Going back...")
                _send_main_menu(from_phone)
            else:
                _send_main_menu(from_phone)
            return jsonify({"ok": True})
        
        # Default: Ask AI
        result = ask_guarded({"question": text, "account_id": account_id, "lang": "en", "channel": "whatsapp"})
        if result.get("ok"):
            answer = result.get("answer", "")
            if answer:
                send_whatsapp_text(from_phone, answer + "\n\n💡 Reply 8 for main menu, or # to save this conversation.")
            else:
                send_whatsapp_text(from_phone, "I couldn't find an answer. Reply 8 for menu.\n\n💡 # - Save & Menu | 0 - Cancel")
        else:
            send_whatsapp_text(from_phone, "Sorry, I encountered an error. Reply 8 for menu.\n\n💡 # - Save & Menu | 0 - Cancel")
        
        return jsonify({"ok": True})
        
    except Exception as e:
        logging.exception(f"WA webhook error: {e}")
        return jsonify({"ok": True})
