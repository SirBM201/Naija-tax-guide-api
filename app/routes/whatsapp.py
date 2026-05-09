# app/routes/whatsapp.py
from __future__ import annotations

import os
import re
import logging
import random
import calendar
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
CALC_NUMBER_RE = re.compile(r"^[1-6]$")

user_states = {}

# ============ TAX CALCULATION FUNCTIONS ============

def calculate_paye(monthly_gross):
    """Calculate Nigerian PAYE tax"""
    annual_gross = monthly_gross * 12
    pension = monthly_gross * 0.08
    nhf = monthly_gross * 0.025
    
    cra_fixed = 200000
    cra_one_percent = annual_gross * 0.01
    cra_base = max(cra_fixed, cra_one_percent)
    cra_percentage = annual_gross * 0.20
    cra_total = cra_base + cra_percentage
    
    total_deductions = (pension * 12) + (nhf * 12) + cra_total
    chargeable = max(0, annual_gross - total_deductions)
    
    if chargeable <= 300000:
        annual_tax = chargeable * 0.07
    elif chargeable <= 600000:
        annual_tax = 21000 + (chargeable - 300000) * 0.11
    elif chargeable <= 1100000:
        annual_tax = 54000 + (chargeable - 600000) * 0.15
    elif chargeable <= 1600000:
        annual_tax = 129000 + (chargeable - 1100000) * 0.19
    elif chargeable <= 3200000:
        annual_tax = 224000 + (chargeable - 1600000) * 0.21
    else:
        annual_tax = 560000 + (chargeable - 3200000) * 0.24
    
    if annual_tax < annual_gross * 0.01:
        annual_tax = annual_gross * 0.01
    
    monthly_tax = annual_tax / 12
    rate = (annual_tax / annual_gross) * 100 if annual_gross > 0 else 0
    
    return {
        "gross": monthly_gross,
        "annual_gross": annual_gross,
        "pension": round(pension),
        "nhf": round(nhf),
        "tax": round(monthly_tax),
        "annual_tax": round(annual_tax),
        "net": round(monthly_gross - pension - nhf - monthly_tax),
        "rate": round(rate, 1)
    }

def calculate_cit(turnover, profit=None):
    """Calculate Nigerian Company Income Tax"""
    if profit is None:
        profit = turnover * 0.20
    if turnover < 25000000:
        size = "Small (Exempt)"
        rate = 0
    elif turnover <= 100000000:
        size = "Medium"
        rate = 20
    else:
        size = "Large"
        rate = 30
    cit = profit * rate / 100
    education = profit * 0.03
    total = cit + education
    return {"turnover": turnover, "profit": profit, "size": size, "rate": rate, "total": round(total)}

def calculate_vat(amount, inclusive=False):
    """Calculate Nigerian VAT (7.5%)"""
    if inclusive:
        vat = amount * 0.075 / 1.075
        exclusive = amount - vat
        total = amount
    else:
        vat = amount * 0.075
        exclusive = amount
        total = amount + vat
    return {"amount": amount, "vat": round(vat), "exclusive": round(exclusive), "total": round(total)}

WHT_RATES = {
    "consultancy": 10, "rent": 10, "interest": 10, "dividend": 10,
    "construction": 5, "contracts": 5, "transport": 3
}

def calculate_wht(amount, trans_type):
    """Calculate Withholding Tax"""
    rate = WHT_RATES.get(trans_type, 10)
    wht = amount * rate / 100
    return {"amount": amount, "rate": rate, "wht": round(wht), "net": round(amount - wht)}

def get_comparison_result(salaries):
    """Format salary comparison result"""
    if len(salaries) < 2:
        return "Need at least 2 salaries to compare."
    msg = "*📊 SALARY COMPARISON RESULT*\n\n"
    for i, s in enumerate(salaries, 1):
        msg += f"{i}. ₦{s['gross']:,.0f} → ₦{s['net']:,.0f} net (Tax: ₦{s['tax']:,.0f}, Rate: {s['rate']}%)\n"
    best = max(salaries, key=lambda x: x['net'])
    msg += f"\n✅ *Best take-home:* ₦{best['gross']:,.0f} → ₦{best['net']:,.0f}"
    return msg

TAX_CALENDAR = {
    1: {14: "PAYE Remittance (Dec)", 21: "VAT Filing (Dec)"},
    2: {14: "PAYE Remittance (Jan)", 21: "VAT Filing (Jan)"},
    3: {14: "PAYE Remittance (Feb)", 21: "VAT Filing (Feb)", 31: "Annual CIT Filing"},
    4: {14: "PAYE Remittance (Mar)", 21: "VAT Filing (Mar)", 30: "Q1 CIT Filing"},
    5: {14: "PAYE Remittance (Apr)", 21: "VAT Filing (Apr)"},
    6: {14: "PAYE Remittance (May)", 21: "VAT Filing (May)"},
    7: {14: "PAYE Remittance (Jun)", 21: "VAT Filing (Jun)", 31: "Q2 CIT Filing"},
    8: {14: "PAYE Remittance (Jul)", 21: "VAT Filing (Jul)"},
    9: {14: "PAYE Remittance (Aug)", 21: "VAT Filing(Aug)"},
    10: {14: "PAYE Remittance (Sep)", 21: "VAT Filing (Sep)", 31: "Q3 CIT Filing"},
    11: {14: "PAYE Remittance (Oct)", 21: "VAT Filing (Oct)"},
    12: {14: "PAYE Remittance (Nov)", 21: "VAT Filing (Nov)", 31: "Year-end Planning"},
}

QUIZ_QUESTIONS = [
    {"q": "What is the current VAT rate in Nigeria?", "opt": ["5%", "7.5%", "10%", "12.5%"], "correct": 1, "exp": "VAT rate in Nigeria is 7.5%"},
    {"q": "By which date must PAYE be remitted monthly?", "opt": ["7th", "14th", "21st", "30th"], "correct": 1, "exp": "PAYE must be remitted by the 14th of each month"},
    {"q": "What is the CIT rate for large companies?", "opt": ["20%", "25%", "30%", "35%"], "correct": 2, "exp": "Large companies pay 30% CIT + 3% Education Tax"},
    {"q": "What is the WHT rate for consultancy services?", "opt": ["5%", "7.5%", "10%", "12.5%"], "correct": 2, "exp": "Consultancy services attract 10% Withholding Tax"},
]

<<<<<<< HEAD
# ============ MENU FUNCTIONS ============

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
        "1️⃣ - Tax Calculator (PAYE, CIT, VAT, WHT)\n"
        "2️⃣ - File PAYE Tax\n"
        "3️⃣ - File VAT\n"
        "4️⃣ - File CIT\n"
        "5️⃣ - View Filing History\n"
        "6️⃣ - View Tax Deadlines\n"
        "7️⃣ - Back to Main Menu\n\n"
        "💡 Global commands:\n"
        "# - Save & Menu | * - Back | 0 - Cancel | 9 - Resume"
    )
    send_whatsapp_text(phone, menu)

def _send_tax_calculator_menu(phone: str):
    menu = (
=======
def get_tax_calculator_menu():
    return (
>>>>>>> 5aa809ef2bfe2c6d95bde08c459f82d9b0747ce1
        "*🧮 TAX CALCULATOR*\n\n"
        "Reply with:\n"
        "1️⃣ - PAYE Tax Calculator\n"
        "2️⃣ - Company Income Tax (CIT)\n"
        "3️⃣ - VAT Calculator\n"
        "4️⃣ - Withholding Tax (WHT)\n"
        "5️⃣ - Salary Comparison\n"
        "6️⃣ - Tax Quiz\n"
        "7️⃣ - Tax Calendar & Deadlines\n"
        "8️⃣ - Back to Tax Filing Menu\n\n"
        "💡 Global commands:\n"
        "# - Save & Menu | * - Back | 0 - Cancel | 9 - Resume"
    )
<<<<<<< HEAD
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
        "💡 Global commands (anytime):\n"
        "# - Save & Menu | * - Back | 0 - Cancel | 9 - Resume\n\n"
        "Or just type your tax question!"
    )
    send_whatsapp_text(phone, welcome)

# ============ HELPER FUNCTIONS ============
=======

def get_tax_menu():
    return (
        "*📋 TAX FILING & MANAGEMENT*\n\n"
        "Reply with:\n"
        "1️⃣ - Tax Calculator (PAYE, CIT, VAT, WHT)\n"
        "2️⃣ - File PAYE Tax\n"
        "3️⃣ - File VAT\n"
        "4️⃣ - File CIT\n"
        "5️⃣ - View Filing History\n"
        "6️⃣ - View Tax Deadlines\n"
        "7️⃣ - Back to Main Menu\n\n"
        "💡 Global commands:\n"
        "# - Save & Menu | * - Back | 0 - Cancel | 9 - Resume"
    )

# ============ EXISTING FUNCTIONS (kept as is) ============
>>>>>>> 5aa809ef2bfe2c6d95bde08c459f82d9b0747ce1

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

<<<<<<< HEAD
=======

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


>>>>>>> 5aa809ef2bfe2c6d95bde08c459f82d9b0747ce1
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

<<<<<<< HEAD
=======

>>>>>>> 5aa809ef2bfe2c6d95bde08c459f82d9b0747ce1
def _show_filing_step(phone: str, tax_type: str, step: int, inputs: dict):
    if tax_type == "paye":
        if step == 1:
            send_whatsapp_text(phone, "📋 *PAYE Tax Filing - Step 1 of 3*\n\nWhat is your monthly salary?\n(Example: 750000 or 750k)\n\n💡 * - Back | # - Save & Menu | 0 - Cancel")
        elif step == 2:
            send_whatsapp_text(phone, f"✅ Received: ₦{inputs.get('monthly_gross_income', 0):,.2f}\n\n📋 Step 2 of 3: Pension Contribution\nEnter your monthly pension contribution (0 if none):\n\n💡 * - Back | # - Save & Menu | 0 - Cancel")
        elif step == 3:
            send_whatsapp_text(phone, f"✅ Received: ₦{inputs.get('pension_contribution', 0):,.2f}\n\n📋 Step 3 of 3: NHF Contribution\nEnter your NHF contribution (0 if none):\n\n💡 * - Back | # - Save & Menu | 0 - Cancel")
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
        send_whatsapp_text(phone, "📋 No tax filings found. Reply with 2 to file PAYE tax, 3 for VAT, or 4 for CIT under Tax menu.")

<<<<<<< HEAD
def _handle_tax_calendar(phone: str):
    """Show tax calendar"""
    today = datetime.now()
    month = today.month
    year = today.year
    month_name = ["January", "February", "March", "April", "May", "June", 
                  "July", "August", "September", "October", "November", "December"][month - 1]
    
    deadlines = TAX_CALENDAR.get(month, {})
    
    msg = f"*📅 {month_name} {year} - Tax Calendar*\n\n"
    
    if deadlines:
        for day, name in sorted(deadlines.items()):
            msg += f"🔴 *{day} {month_name}:* {name}\n"
        msg += "\n📌 *Upcoming Deadlines:*\n"
        
        today_dt = datetime.now()
        for i in range(1, 31):
            check_date = today_dt + timedelta(days=i)
            check_month = check_date.month
            check_day = check_date.day
            month_deadlines = TAX_CALENDAR.get(check_month, {})
            if check_day in month_deadlines:
                msg += f"📅 {check_date.strftime('%b %d')}: {month_deadlines[check_day]}\n"
    else:
        msg += "✅ No tax deadlines this month\n"
    
    send_whatsapp_text(phone, msg)

# ============ CALCULATOR HANDLERS ============

def _handle_paye_calculator(phone: str, account_id: str, text: str = None, step: int = 1):
    """Handle PAYE calculator flow"""
    if step == 1:
        user_states[phone] = {"context": "paye_calc", "step": 1}
        send_whatsapp_text(phone, "💰 *PAYE Calculator*\n\nEnter your monthly salary:\n(Example: 500000 or 500k)\n\n💡 * - Back | # - Save & Menu | 0 - Cancel")
        return True
    
    if text:
        try:
            amount = _parse_amount(text)
            result = calculate_paye(amount)
            msg = (f"*📊 PAYE CALCULATION RESULT*\n\n"
                   f"💰 Monthly Gross: ₦{result['gross']:,.0f}\n"
                   f"📈 Annual Gross: ₦{result['annual_gross']:,.0f}\n"
                   f"📋 Pension (8%): ₦{result['pension']:,.0f}\n"
                   f"📋 NHF (2.5%): ₦{result['nhf']:,.0f}\n"
                   f"🧾 Monthly Tax: *₦{result['tax']:,.0f}*\n"
                   f"🧾 Annual Tax: ₦{result['annual_tax']:,.0f}\n"
                   f"💵 Net Pay: *₦{result['net']:,.0f}*\n"
                   f"📊 Effective Rate: {result['rate']}%\n\n"
                   f"Reply 1 to calculate another, or * to go back.")
            send_whatsapp_text(phone, msg)
            return True
        except ValueError:
            send_whatsapp_text(phone, "❌ Invalid amount. Please enter a valid number (e.g., 500000 or 500k)")
            return True
    return False

def _handle_cit_calculator(phone: str, account_id: str, text: str = None, step: int = 1):
    """Handle CIT calculator flow"""
    if step == 1:
        user_states[phone] = {"context": "cit_calc", "step": 1}
        send_whatsapp_text(phone, "🏢 *CIT Calculator*\n\nEnter your company's annual turnover:\n(Example: 50000000 or 50M)\n\n💡 * - Back | # - Save & Menu | 0 - Cancel")
        return True
    
    if text:
        try:
            amount = _parse_amount(text)
            result = calculate_cit(amount)
            msg = (f"*📊 CIT CALCULATION RESULT*\n\n"
                   f"📊 Annual Turnover: ₦{result['turnover']:,.0f}\n"
                   f"📈 Taxable Profit: ₦{result['profit']:,.0f}\n"
                   f"🏷️ Company Size: {result['size']}\n"
                   f"📊 Tax Rate: {result['rate']}%\n"
                   f"🧾 CIT Payable: *₦{result['total']:,.0f}*\n\n"
                   f"Reply 2 to calculate another, or * to go back.")
            send_whatsapp_text(phone, msg)
            return True
        except ValueError:
            send_whatsapp_text(phone, "❌ Invalid amount. Please enter a valid number (e.g., 50000000 or 50M)")
            return True
    return False

def _handle_vat_calculator(phone: str, account_id: str, text: str = None, step: int = 1):
    """Handle VAT calculator flow"""
    if step == 1:
        user_states[phone] = {"context": "vat_calc", "step": 1}
        send_whatsapp_text(phone, "🧾 *VAT Calculator*\n\n1️⃣ - Add VAT (exclusive amount)\n2️⃣ - Extract VAT (inclusive amount)\n\n💡 * - Back | # - Save & Menu | 0 - Cancel")
        return True
    
    if step == 2 and text in ["1", "2"]:
        user_states[phone] = {"context": "vat_calc", "step": 2, "mode": "exclusive" if text == "1" else "inclusive"}
        mode_text = "exclusive (without VAT)" if text == "1" else "inclusive (with VAT)"
        send_whatsapp_text(phone, f"🧾 *VAT Calculator*\n\nEnter amount ({mode_text}):\n(Example: 100000)\n\n💡 * - Back | # - Save & Menu | 0 - Cancel")
        return True
    
    if step == 2 and text not in ["1", "2"]:
        send_whatsapp_text(phone, "❌ Please reply with 1 or 2")
        return True
    
    if text and step >= 2:
        try:
            amount = _parse_amount(text)
            mode = user_states.get(phone, {}).get("mode", "exclusive")
            result = calculate_vat(amount, inclusive=(mode == "inclusive"))
            
            if mode == "exclusive":
                msg = (f"*📊 VAT CALCULATION RESULT*\n\n"
                       f"💰 Amount (excl. VAT): ₦{result['amount']:,.0f}\n"
                       f"📊 VAT (7.5%): ₦{result['vat']:,.0f}\n"
                       f"💰 Total (incl. VAT): *₦{result['total']:,.0f}*")
            else:
                msg = (f"*📊 VAT CALCULATION RESULT*\n\n"
                       f"💰 Amount (incl. VAT): ₦{result['amount']:,.0f}\n"
                       f"📊 VAT (7.5%): ₦{result['vat']:,.0f}\n"
                       f"💰 Amount (excl. VAT): *₦{result['exclusive']:,.0f}*")
            
            send_whatsapp_text(phone, msg + "\n\nReply 3 to calculate another, or * to go back.")
            return True
        except ValueError:
            send_whatsapp_text(phone, "❌ Invalid amount. Please enter a valid number (e.g., 100000)")
            return True
    return False

def _handle_wht_calculator(phone: str, account_id: str, text: str = None, step: int = 1):
    """Handle WHT calculator flow"""
    if step == 1:
        user_states[phone] = {"context": "wht_calc", "step": 1}
        send_whatsapp_text(phone, "📊 *WHT Calculator*\n\nEnter the payment amount:\n(Example: 500000)\n\n💡 * - Back | # - Save & Menu | 0 - Cancel")
        return True
    
    if step == 1 and text:
        try:
            amount = _parse_amount(text)
            user_states[phone] = {"context": "wht_calc", "step": 2, "amount": amount}
            send_whatsapp_text(phone, "📊 *WHT Calculator*\n\nEnter transaction type:\n\n• consultancy\n• rent\n• interest\n• dividend\n• construction\n• contracts\n• transport\n\n💡 * - Back | # - Save & Menu | 0 - Cancel")
            return True
        except ValueError:
            send_whatsapp_text(phone, "❌ Invalid amount. Please enter a valid number (e.g., 500000)")
            return True
    
    if step == 2 and text:
        trans_type = text.lower()
        if trans_type not in WHT_RATES:
            send_whatsapp_text(phone, "❌ Invalid type. Please choose: consultancy, rent, interest, dividend, construction, contracts, transport")
            return True
        
        amount = user_states.get(phone, {}).get("amount", 0)
        result = calculate_wht(amount, trans_type)
        msg = (f"*📊 WHT CALCULATION RESULT*\n\n"
               f"💰 Payment Amount: ₦{result['amount']:,.0f}\n"
               f"📋 Transaction: {trans_type}\n"
               f"📊 WHT Rate: {result['rate']}%\n"
               f"🧾 *WHT to Deduct: ₦{result['wht']:,.0f}*\n"
               f"💵 Net Payment: ₦{result['net']:,.0f}\n\n"
               f"Reply 4 to calculate another, or * to go back.")
        send_whatsapp_text(phone, msg)
        return True
    
    return False

def _handle_salary_comparison(phone: str, account_id: str, text: str = None):
    """Handle salary comparison flow"""
    state = user_states.get(phone, {})
    
    if state.get("context") != "salary_compare":
        user_states[phone] = {"context": "salary_compare", "salaries": [], "step": 1}
        send_whatsapp_text(phone, "📊 *Salary Comparison*\n\nSend up to 5 salaries. Send 'done' when finished.\n\nSend salary 1 (e.g., 500000):\n\n💡 * - Back | # - Save & Menu | 0 - Cancel")
        return True
    
    if text and text.lower() == "done":
        salaries = state.get("salaries", [])
        if len(salaries) < 2:
            send_whatsapp_text(phone, "❌ Need at least 2 salaries to compare. Send another salary or type 'cancel'.")
            return True
        result = get_comparison_result(salaries)
        send_whatsapp_text(phone, result)
        user_states.pop(phone, None)
        return True
    
    if text:
        try:
            amount = _parse_amount(text)
            salaries = state.get("salaries", [])
            result = calculate_paye(amount)
            salaries.append(result)
            user_states[phone] = {"context": "salary_compare", "salaries": salaries, "step": len(salaries) + 1}
            
            if len(salaries) >= 5:
                msg = f"✅ Added ₦{amount:,.0f}\n\nYou have 5 salaries. Type 'done' to see comparison."
            else:
                msg = f"✅ Added ₦{amount:,.0f}\n\nSend salary {len(salaries) + 1} (or type 'done'):"
            send_whatsapp_text(phone, msg)
            return True
        except:
            send_whatsapp_text(phone, "❌ Invalid amount. Please enter a valid number (e.g., 500000)")
            return True
    
    return False

def _handle_tax_quiz(phone: str, account_id: str, text: str = None):
    """Handle tax quiz flow"""
    state = user_states.get(phone, {})
    
    if state.get("context") != "tax_quiz":
        q = random.choice(QUIZ_QUESTIONS)
        opts = "\n".join([f"{i+1}. {opt}" for i, opt in enumerate(q['opt'])])
        user_states[phone] = {"context": "tax_quiz", "question": q, "step": 1}
        send_whatsapp_text(phone, f"📚 *TAX QUIZ*\n\n{q['q']}\n\n{opts}\n\nReply with number (1-4):\n\n💡 * - Back | # - Save & Menu | 0 - Cancel")
        return True
    
    if text and text in ["1", "2", "3", "4"]:
        q = state.get("question")
        selected = int(text) - 1
        if selected == q['correct']:
            send_whatsapp_text(phone, f"✅ *Correct!* {q.get('exp', 'Well done!')}\n\nSend 6 for another quiz question!")
        else:
            correct_opt = q['opt'][q['correct']]
            send_whatsapp_text(phone, f"❌ *Incorrect!* The correct answer is {correct_opt}.\n\n{q.get('exp', '')}\n\nSend 6 for another quiz question!")
        user_states.pop(phone, None)
        return True
    
    if text:
        send_whatsapp_text(phone, "❌ Please reply with 1, 2, 3, or 4")
        return True
    
    return False

def _handle_tax_calculator_menu_selection(phone: str, account_id: str, text: str):
    """Handle selections from tax calculator menu (Options 1-8)"""
    if text == "1":
        _handle_paye_calculator(phone, account_id)
    elif text == "2":
        _handle_cit_calculator(phone, account_id)
    elif text == "3":
        _handle_vat_calculator(phone, account_id)
    elif text == "4":
        _handle_wht_calculator(phone, account_id)
    elif text == "5":
        _handle_salary_comparison(phone, account_id)
    elif text == "6":
        _handle_tax_quiz(phone, account_id)
    elif text == "7":
        _handle_tax_calendar(phone)
    elif text == "8":
        _send_tax_menu(phone)
    else:
        send_whatsapp_text(phone, "❌ Invalid option. Please reply with 1-8.")

# ============ FILING HANDLERS ============

=======
>>>>>>> 5aa809ef2bfe2c6d95bde08c459f82d9b0747ce1
def _handle_paye_filing(phone: str, account_id: str, step: int, inputs: dict, text: str):
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
    
    return False

<<<<<<< HEAD
=======

>>>>>>> 5aa809ef2bfe2c6d95bde08c459f82d9b0747ce1
def _handle_vat_filing(phone: str, account_id: str, step: int, inputs: dict, text: str):
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
    
    return False

<<<<<<< HEAD
=======

>>>>>>> 5aa809ef2bfe2c6d95bde08c459f82d9b0747ce1
def _handle_cit_filing(phone: str, account_id: str, step: int, inputs: dict, text: str):
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
    
    return False

def _handle_submit(phone: str, account_id: str, user_state: dict):
    sub_context = user_state.get("sub_context")
    inputs = user_state.get("inputs", {})
    
    if sub_context == "paye":
        result = submit_tax_filing(account_id, "paye", inputs, [])
        if result.get("ok"):
            calc = result.get("calculation", {})
            monthly_tax = calc.get("monthly_tax_payable", 0)
            reference = result.get("reference", "N/A")
            send_whatsapp_text(phone, f"✅ *PAYE Filing Submitted!*\n\n📋 Reference: {reference}\n💰 Monthly Tax: ₦{monthly_tax:,.2f}\n\nReply 8 for main menu.")
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
            send_whatsapp_text(phone, f"✅ *VAT Filing Submitted!*\n\n📋 Reference: {reference}\n💰 VAT Payable: ₦{vat_payable:,.2f}")
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
            send_whatsapp_text(phone, f"✅ *CIT Filing Submitted!*\n\n📋 Reference: {reference}\n💰 CIT Payable: ₦{cit_payable:,.2f}")
        else:
            send_whatsapp_text(phone, f"❌ Filing failed: {result.get('error', 'Unknown error')}")
    
    delete_filing_draft(account_id, sub_context)
    user_states.pop(phone, None)

<<<<<<< HEAD
=======

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
        send_whatsapp_text(phone, "📋 No tax filings found. Reply with 2 to file PAYE tax, 3 for VAT, or 4 for CIT under Tax menu.")

# ============ TAX CALCULATOR HANDLERS ============

def _send_tax_calculator_menu(phone: str):
    send_whatsapp_text(phone, get_tax_calculator_menu())

def _send_tax_menu(phone: str):
    send_whatsapp_text(phone, get_tax_menu())

def _handle_paye_calculator(phone: str, account_id: str, text: str, step: int = 1):
    """Handle PAYE calculator flow"""
    if step == 1:
        user_states[phone] = {"context": "paye_calc", "step": 1}
        send_whatsapp_text(phone, "💰 *PAYE Calculator*\n\nEnter your monthly salary:\n(Example: 500000 or 500k)\n\n💡 * - Back | # - Save & Menu | 0 - Cancel")
        return True
    else:
        try:
            amount = _parse_amount(text)
            result = calculate_paye(amount)
            msg = (f"*📊 PAYE CALCULATION RESULT*\n\n"
                   f"💰 Monthly Gross: ₦{result['gross']:,.0f}\n"
                   f"📈 Annual Gross: ₦{result['annual_gross']:,.0f}\n"
                   f"📋 Pension (8%): ₦{result['pension']:,.0f}\n"
                   f"📋 NHF (2.5%): ₦{result['nhf']:,.0f}\n"
                   f"🧾 Monthly Tax: *₦{result['tax']:,.0f}*\n"
                   f"🧾 Annual Tax: ₦{result['annual_tax']:,.0f}\n"
                   f"💵 Net Pay: *₦{result['net']:,.0f}*\n"
                   f"📊 Effective Rate: {result['rate']}%\n\n"
                   f"Reply with another amount to calculate again,\n"
                   f"or send * to go back to calculator menu.")
            send_whatsapp_text(phone, msg)
            # Stay in calculator mode for another calculation
            return True
        except ValueError:
            send_whatsapp_text(phone, "❌ Invalid amount. Please enter a valid number (e.g., 500000 or 500k)")
            return True

def _handle_cit_calculator(phone: str, account_id: str, text: str, step: int = 1):
    """Handle CIT calculator flow"""
    if step == 1:
        user_states[phone] = {"context": "cit_calc", "step": 1}
        send_whatsapp_text(phone, "🏢 *CIT Calculator*\n\nEnter your company's annual turnover:\n(Example: 50000000 or 50M)\n\n💡 * - Back | # - Save & Menu | 0 - Cancel")
        return True
    else:
        try:
            amount = _parse_amount(text)
            result = calculate_cit(amount)
            msg = (f"*📊 CIT CALCULATION RESULT*\n\n"
                   f"📊 Annual Turnover: ₦{result['turnover']:,.0f}\n"
                   f"📈 Taxable Profit: ₦{result['profit']:,.0f}\n"
                   f"🏷️ Company Size: {result['size']}\n"
                   f"📊 Tax Rate: {result['rate']}%\n"
                   f"🧾 CIT Payable: *₦{result['total']:,.0f}*\n\n"
                   f"Reply with another turnover to calculate again,\n"
                   f"or send * to go back to calculator menu.")
            send_whatsapp_text(phone, msg)
            return True
        except ValueError:
            send_whatsapp_text(phone, "❌ Invalid amount. Please enter a valid number (e.g., 50000000 or 50M)")
            return True

def _handle_vat_calculator(phone: str, account_id: str, text: str, step: int = 1):
    """Handle VAT calculator flow"""
    if step == 1:
        user_states[phone] = {"context": "vat_calc", "step": 1, "substep": 1}
        send_whatsapp_text(phone, "🧾 *VAT Calculator*\n\n1️⃣ - Add VAT (exclusive amount)\n2️⃣ - Extract VAT (inclusive amount)\n\n💡 * - Back | # - Save & Menu | 0 - Cancel")
        return True
    elif step == 2:
        # User selected inclusive vs exclusive
        if text in ["1", "2"]:
            user_states[phone] = {"context": "vat_calc", "step": 2, "mode": "exclusive" if text == "1" else "inclusive"}
            mode_text = "exclusive (without VAT)" if text == "1" else "inclusive (with VAT)"
            send_whatsapp_text(phone, f"🧾 *VAT Calculator*\n\nEnter amount ({mode_text}):\n(Example: 100000)\n\n💡 * - Back | # - Save & Menu | 0 - Cancel")
            return True
        else:
            send_whatsapp_text(phone, "❌ Please reply with 1 or 2")
            return True
    else:
        try:
            amount = _parse_amount(text)
            mode = user_states[phone].get("mode", "exclusive")
            result = calculate_vat(amount, inclusive=(mode == "inclusive"))
            
            if mode == "exclusive":
                msg = (f"*📊 VAT CALCULATION RESULT*\n\n"
                       f"💰 Amount (excl. VAT): ₦{result['amount']:,.0f}\n"
                       f"📊 VAT (7.5%): ₦{result['vat']:,.0f}\n"
                       f"💰 Total (incl. VAT): *₦{result['total']:,.0f}*")
            else:
                msg = (f"*📊 VAT CALCULATION RESULT*\n\n"
                       f"💰 Amount (incl. VAT): ₦{result['amount']:,.0f}\n"
                       f"📊 VAT (7.5%): ₦{result['vat']:,.0f}\n"
                       f"💰 Amount (excl. VAT): *₦{result['exclusive']:,.0f}*")
            
            send_whatsapp_text(phone, msg + "\n\nReply with another amount to calculate again,\nor send * to go back to calculator menu.")
            return True
        except ValueError:
            send_whatsapp_text(phone, "❌ Invalid amount. Please enter a valid number (e.g., 100000)")
            return True

def _handle_wht_calculator(phone: str, account_id: str, text: str, step: int = 1):
    """Handle WHT calculator flow"""
    if step == 1:
        user_states[phone] = {"context": "wht_calc", "step": 1}
        send_whatsapp_text(phone, "📊 *WHT Calculator*\n\nEnter the payment amount:\n(Example: 500000)\n\n💡 * - Back | # - Save & Menu | 0 - Cancel")
        return True
    elif step == 2:
        # We have the amount, now ask for transaction type
        user_states[phone] = {"context": "wht_calc", "step": 2, "amount": text}
        send_whatsapp_text(phone, "📊 *WHT Calculator*\n\nEnter transaction type:\n\n• consultancy\n• rent\n• interest\n• dividend\n• construction\n• contracts\n• transport\n\n💡 * - Back | # - Save & Menu | 0 - Cancel")
        return True
    else:
        try:
            trans_type = text.lower()
            if trans_type not in WHT_RATES:
                send_whatsapp_text(phone, "❌ Invalid type. Please choose: consultancy, rent, interest, dividend, construction, contracts, transport")
                return True
            
            amount = float(user_states[phone].get("amount", "0"))
            result = calculate_wht(amount, trans_type)
            msg = (f"*📊 WHT CALCULATION RESULT*\n\n"
                   f"💰 Payment Amount: ₦{result['amount']:,.0f}\n"
                   f"📋 Transaction: {trans_type}\n"
                   f"📊 WHT Rate: {result['rate']}%\n"
                   f"🧾 *WHT to Deduct: ₦{result['wht']:,.0f}*\n"
                   f"💵 Net Payment: ₦{result['net']:,.0f}\n\n"
                   f"Reply with another amount to calculate again,\n"
                   f"or send * to go back to calculator menu.")
            send_whatsapp_text(phone, msg)
            # Reset to allow new calculation
            user_states[phone] = {"context": "wht_calc", "step": 1}
            return True
        except:
            send_whatsapp_text(phone, "❌ Error. Please try again.")
            return True

def _handle_salary_comparison(phone: str, account_id: str, text: str):
    """Handle salary comparison flow"""
    state = user_states.get(phone, {})
    if state.get("context") != "salary_compare":
        user_states[phone] = {"context": "salary_compare", "salaries": [], "step": 1}
        send_whatsapp_text(phone, "📊 *Salary Comparison*\n\nSend up to 5 salaries. Send 'done' when finished.\n\nSend salary 1 (e.g., 500000):\n\n💡 * - Back | # - Save & Menu | 0 - Cancel")
        return True
    
    if text.lower() == "done":
        salaries = state.get("salaries", [])
        if len(salaries) < 2:
            send_whatsapp_text(phone, "❌ Need at least 2 salaries to compare. Send another salary or type 'cancel'.")
            return True
        result = get_comparison_result(salaries)
        send_whatsapp_text(phone, result)
        user_states.pop(phone, None)
        return True
    
    try:
        amount = _parse_amount(text)
        salaries = state.get("salaries", [])
        result = calculate_paye(amount)
        salaries.append(result)
        user_states[phone] = {"context": "salary_compare", "salaries": salaries, "step": len(salaries) + 1}
        
        if len(salaries) >= 5:
            msg = f"✅ Added ₦{amount:,.0f}\n\nYou have 5 salaries. Type 'done' to see comparison."
        else:
            msg = f"✅ Added ₦{amount:,.0f}\n\nSend salary {len(salaries) + 1} (or type 'done'):"
        send_whatsapp_text(phone, msg)
        return True
    except:
        send_whatsapp_text(phone, "❌ Invalid amount. Please enter a valid number (e.g., 500000)")
        return True

def _handle_tax_quiz(phone: str, account_id: str, text: str):
    """Handle tax quiz flow"""
    state = user_states.get(phone, {})
    if state.get("context") != "tax_quiz":
        q = random.choice(QUIZ_QUESTIONS)
        opts = "\n".join([f"{i+1}. {opt}" for i, opt in enumerate(q['opt'])])
        user_states[phone] = {"context": "tax_quiz", "question": q, "step": 1}
        send_whatsapp_text(phone, f"📚 *TAX QUIZ*\n\n{q['q']}\n\n{opts}\n\nReply with number (1-4):\n\n💡 * - Back | # - Save & Menu | 0 - Cancel")
        return True
    
    if text in ["1", "2", "3", "4"]:
        q = state.get("question")
        selected = int(text) - 1
        if selected == q['correct']:
            send_whatsapp_text(phone, f"✅ *Correct!* {q.get('exp', 'Well done!')}\n\nSend 7 again for another quiz question!")
        else:
            correct_opt = q['opt'][q['correct']]
            send_whatsapp_text(phone, f"❌ *Incorrect!* The correct answer is {correct_opt}.\n\n{q.get('exp', '')}\n\nSend 7 again for another quiz question!")
        user_states.pop(phone, None)
        return True
    else:
        send_whatsapp_text(phone, "❌ Please reply with 1, 2, 3, or 4")
        return True

def _handle_tax_calendar(phone: str):
    """Show tax calendar"""
    today = datetime.now()
    month = today.month
    year = today.year
    month_name = ["January", "February", "March", "April", "May", "June", 
                  "July", "August", "September", "October", "November", "December"][month - 1]
    
    deadlines = TAX_CALENDAR.get(month, {})
    
    msg = f"*📅 {month_name} {year} - Tax Calendar*\n\n"
    
    if deadlines:
        for day, name in sorted(deadlines.items()):
            msg += f"🔴 *{day} {month_name}:* {name}\n"
        msg += "\n📌 *Upcoming Deadlines:*\n"
        
        # Show next 30 days deadlines
        today_dt = datetime.now()
        for i in range(1, 31):
            check_date = today_dt + timedelta(days=i)
            check_month = check_date.month
            check_day = check_date.day
            month_deadlines = TAX_CALENDAR.get(check_month, {})
            if check_day in month_deadlines:
                msg += f"📅 {check_date.strftime('%b %d')}: {month_deadlines[check_day]}\n"
    else:
        msg += "✅ No tax deadlines this month\n"
    
    send_whatsapp_text(phone, msg)

def _handle_tax_calculator_menu_selection(phone: str, account_id: str, text: str):
    """Handle selections from tax calculator menu (Options 1-8)"""
    if text == "1":
        _handle_paye_calculator(phone, account_id, "", step=1)
    elif text == "2":
        _handle_cit_calculator(phone, account_id, "", step=1)
    elif text == "3":
        _handle_vat_calculator(phone, account_id, "", step=1)
    elif text == "4":
        _handle_wht_calculator(phone, account_id, "", step=1)
    elif text == "5":
        _handle_salary_comparison(phone, account_id, "")
    elif text == "6":
        _handle_tax_quiz(phone, account_id, "")
    elif text == "7":
        _handle_tax_calendar(phone)
    elif text == "8":
        _send_tax_menu(phone)
    else:
        send_whatsapp_text(phone, "❌ Invalid option. Please reply with 1-8.")

>>>>>>> 5aa809ef2bfe2c6d95bde08c459f82d9b0747ce1
# ============ MAIN WEBHOOK ============

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
        
        # DEBUG ECHO - Send immediate acknowledgment
        send_whatsapp_text(from_phone, f"✅ Received: {text if text else 'message'}\n\nReply 8 for main menu.")
        
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
        
<<<<<<< HEAD
        # Handle email collection
=======
        # Handle email collection (existing)
>>>>>>> 5aa809ef2bfe2c6d95bde08c459f82d9b0747ce1
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
                send_whatsapp_text(from_phone, "❌ Invalid email. Send a valid email, 'cancel' to abort, or '#' to save and exit.")
            return jsonify({"ok": True})
        
<<<<<<< HEAD
        # Global commands
        if text == "#":
            send_whatsapp_text(from_phone, "✅ Progress saved.\n\nReturning to main menu...")
=======
        # ========== GLOBAL COMMANDS ==========
        
        if text == "#":
            current_context = user_state.get("context")
            if current_context in ["filing", "filing_confirm", "paye_calc", "cit_calc", "vat_calc", "wht_calc", "salary_compare", "tax_quiz"]:
                send_whatsapp_text(from_phone, "✅ Progress saved.")
            elif current_context:
                send_whatsapp_text(from_phone, "✅ Progress saved.")
            else:
                send_whatsapp_text(from_phone, "ℹ️ Nothing to save.")
>>>>>>> 5aa809ef2bfe2c6d95bde08c459f82d9b0747ce1
            _send_main_menu(from_phone)
            user_states.pop(from_phone, None)
            return jsonify({"ok": True})
        
        if text == "0":
<<<<<<< HEAD
            if user_state.get("context") in ["filing", "filing_confirm"]:
=======
            current_context = user_state.get("context")
            if current_context in ["filing", "filing_confirm"]:
>>>>>>> 5aa809ef2bfe2c6d95bde08c459f82d9b0747ce1
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
                    "inputs": active["inputs"]
                }
                _show_filing_step(from_phone, active["filing_type"], active["step"], active["inputs"])
            else:
<<<<<<< HEAD
                send_whatsapp_text(from_phone, "📭 No saved filing found. Reply 7 then 1 to start Tax menu.")
            return jsonify({"ok": True})
        
        if text.lower() == "confirm":
            if user_state.get("context") == "filing_confirm":
                _handle_submit(from_phone, account_id, user_state)
            else:
                send_whatsapp_text(from_phone, "No filing to confirm. Reply 7 then 1 to start Tax menu.")
            return jsonify({"ok": True})
        
        if text.lower() == "cancel":
            if user_state.get("context") in ["filing", "filing_confirm"]:
                delete_filing_draft(account_id, user_state.get("sub_context"))
                user_states.pop(from_phone, None)
                send_whatsapp_text(from_phone, "❌ Filing cancelled.\n\nReply 8 for main menu.")
            else:
                send_whatsapp_text(from_phone, "No active filing to cancel.")
            return jsonify({"ok": True})
        
        if text == "*":
            current_context = user_state.get("context")
            if current_context in ["paye_calc", "cit_calc", "vat_calc", "wht_calc", "salary_compare", "tax_quiz"]:
                _send_tax_calculator_menu(from_phone)
                user_states.pop(from_phone, None)
            elif current_context == "filing":
                if user_state.get("step") and user_state.get("step") > 1:
                    new_step = user_state.get("step") - 1
                    user_state["step"] = new_step
                    user_states[from_phone] = user_state
                    _show_filing_step(from_phone, user_state.get("sub_context"), new_step, user_state.get("inputs", {}))
                else:
                    _send_tax_menu(from_phone)
                    user_states.pop(from_phone, None)
            else:
=======
                send_whatsapp_text(from_phone, "📭 No saved filing found. Start a new one with 2, 3, or 4 under Tax menu.")
            return jsonify({"ok": True})
        
        # Handle confirm/cancel for filing
        if text.lower() == "confirm":
            if user_state.get("context") == "filing_confirm":
                _handle_submit(from_phone, account_id, user_state)
            else:
                send_whatsapp_text(from_phone, "No filing to confirm. Reply 7 then 1 to start Tax menu.")
            return jsonify({"ok": True})
        
        if text.lower() == "cancel":
            if user_state.get("context") in ["filing", "filing_confirm"]:
                delete_filing_draft(account_id, user_state.get("sub_context"))
                user_states.pop(from_phone, None)
                send_whatsapp_text(from_phone, "❌ Filing cancelled.\n\nReply 8 for main menu.")
            else:
                send_whatsapp_text(from_phone, "No active filing to cancel.")
            return jsonify({"ok": True})
        
        # Handle back command
        if text == "*":
            current_context = user_state.get("context")
            if current_context in ["paye_calc", "cit_calc", "vat_calc", "wht_calc", "salary_compare", "tax_quiz"]:
                _send_tax_calculator_menu(from_phone)
                user_states.pop(from_phone, None)
            elif current_context == "filing":
                if user_state.get("step") and user_state.get("step") > 1:
                    new_step = user_state.get("step") - 1
                    user_state["step"] = new_step
                    user_states[from_phone] = user_state
                    _show_filing_step(from_phone, user_state.get("sub_context"), new_step, user_state.get("inputs", {}))
                else:
                    _send_tax_menu(from_phone)
                    user_states.pop(from_phone, None)
            elif current_context == "filing_confirm":
                send_whatsapp_text(from_phone, "Type 'cancel' to abort filing.")
            else:
>>>>>>> 5aa809ef2bfe2c6d95bde08c459f82d9b0747ce1
                _send_main_menu(from_phone)
                user_states.pop(from_phone, None)
            return jsonify({"ok": True})
        
<<<<<<< HEAD
        # Check for active filing
=======
        # ========== CHECK FOR ACTIVE FILING ==========
>>>>>>> 5aa809ef2bfe2c6d95bde08c459f82d9b0747ce1
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
        
        if filing_type and step and step < 4:
            if filing_type == "paye":
                _handle_paye_filing(from_phone, account_id, step, inputs, text)
            elif filing_type == "vat":
                _handle_vat_filing(from_phone, account_id, step, inputs, text)
            elif filing_type == "cit":
                _handle_cit_filing(from_phone, account_id, step, inputs, text)
            return jsonify({"ok": True})
        
<<<<<<< HEAD
        # Handle calculator states
        calc_context = user_state.get("context")
        
        if calc_context == "paye_calc":
            _handle_paye_calculator(from_phone, account_id, text, step=2)
            return jsonify({"ok": True})
        
        if calc_context == "cit_calc":
            _handle_cit_calculator(from_phone, account_id, text, step=2)
=======
        # ========== HANDLE TAX CALCULATOR STATES ==========
        calc_context = user_state.get("context")
        
        if calc_context == "paye_calc":
            _handle_paye_calculator(from_phone, account_id, text, step=user_state.get("step", 2))
            return jsonify({"ok": True})
        
        if calc_context == "cit_calc":
            _handle_cit_calculator(from_phone, account_id, text, step=user_state.get("step", 2))
>>>>>>> 5aa809ef2bfe2c6d95bde08c459f82d9b0747ce1
            return jsonify({"ok": True})
        
        if calc_context == "vat_calc":
            current_step = user_state.get("step", 1)
            if current_step == 1:
                _handle_vat_calculator(from_phone, account_id, text, step=1)
<<<<<<< HEAD
            else:
                _handle_vat_calculator(from_phone, account_id, text, step=2)
=======
            elif current_step == 2:
                _handle_vat_calculator(from_phone, account_id, text, step=2)
            else:
                _handle_vat_calculator(from_phone, account_id, text, step=3)
>>>>>>> 5aa809ef2bfe2c6d95bde08c459f82d9b0747ce1
            return jsonify({"ok": True})
        
        if calc_context == "wht_calc":
            current_step = user_state.get("step", 1)
            if current_step == 1:
                _handle_wht_calculator(from_phone, account_id, text, step=1)
<<<<<<< HEAD
            else:
                _handle_wht_calculator(from_phone, account_id, text, step=2)
=======
            elif current_step == 2:
                _handle_wht_calculator(from_phone, account_id, text, step=2)
            else:
                _handle_wht_calculator(from_phone, account_id, text, step=3)
>>>>>>> 5aa809ef2bfe2c6d95bde08c459f82d9b0747ce1
            return jsonify({"ok": True})
        
        if calc_context == "salary_compare":
            _handle_salary_comparison(from_phone, account_id, text)
            return jsonify({"ok": True})
        
        if calc_context == "tax_quiz":
            _handle_tax_quiz(from_phone, account_id, text)
            return jsonify({"ok": True})
        
<<<<<<< HEAD
        # Menu navigation
        if text == "7" or text.lower() == "tax":
=======
        # ========== MENU NAVIGATION ==========
        
        # Tax Filing & Management Menu (from Option 7)
        if text.upper() == "7" or text.lower() == "tax":
>>>>>>> 5aa809ef2bfe2c6d95bde08c459f82d9b0747ce1
            _send_tax_menu(from_phone)
            user_states.pop(from_phone, None)
            return jsonify({"ok": True})
        
<<<<<<< HEAD
=======
        # Handle Tax Menu selections (1-7)
>>>>>>> 5aa809ef2bfe2c6d95bde08c459f82d9b0747ce1
        if user_state.get("context") == "tax_menu":
            if text in ["1", "2", "3", "4", "5", "6", "7"]:
                if text == "1":
                    _send_tax_calculator_menu(from_phone)
                    user_states[from_phone] = {"context": "tax_calculator_menu"}
                elif text == "2":
                    user_states[from_phone] = {"context": "filing", "sub_context": "paye", "step": 1, "inputs": {}}
                    send_whatsapp_text(from_phone, "📋 *PAYE Tax Filing - Step 1 of 3*\n\nWhat is your monthly salary?\n(Example: 750000 or 750k)\n\n💡 * - Back | # - Save & Menu | 0 - Cancel")
                elif text == "3":
                    user_states[from_phone] = {"context": "filing", "sub_context": "vat", "step": 1, "inputs": {}}
                    send_whatsapp_text(from_phone, "📋 *VAT Filing - Step 1 of 3*\n\nWhat is your total sales for the period?\n(Example: 25000000 or 25M)\n\n💡 * - Back | # - Save & Menu | 0 - Cancel")
                elif text == "4":
                    user_states[from_phone] = {"context": "filing", "sub_context": "cit", "step": 1, "inputs": {}}
                    send_whatsapp_text(from_phone, "📋 *CIT Filing - Step 1 of 3*\n\nWhat is your company's total revenue for the period?\n(Example: 50000000 or 50M)\n\n💡 * - Back | # - Save & Menu | 0 - Cancel")
                elif text == "5":
                    _handle_filing_history(from_phone, account_id)
                elif text == "6":
                    _handle_tax_calendar(from_phone)
                elif text == "7":
                    _send_main_menu(from_phone)
                    user_states.pop(from_phone, None)
            else:
                send_whatsapp_text(from_phone, "❌ Invalid option. Please reply with 1-7.")
            return jsonify({"ok": True})
        
<<<<<<< HEAD
=======
        # Tax Calculator Menu selections (1-8)
>>>>>>> 5aa809ef2bfe2c6d95bde08c459f82d9b0747ce1
        if user_state.get("context") == "tax_calculator_menu":
            if text in ["1", "2", "3", "4", "5", "6", "7", "8"]:
                _handle_tax_calculator_menu_selection(from_phone, account_id, text)
            else:
                send_whatsapp_text(from_phone, "❌ Invalid option. Please reply with 1-8.")
            return jsonify({"ok": True})
        
        # Main menu selections (1-8)
        if MENU_NUMBER_RE.match(text):
            option = int(text)
            if option == 7:
                _send_tax_menu(from_phone)
                user_states[from_phone] = {"context": "tax_menu"}
            elif option == 8:
                _send_main_menu(from_phone)
                user_states.pop(from_phone, None)
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
                send_whatsapp_text(from_phone, "❌ *Invalid link code*\n\nGenerate a new code on the website.")
            user_states.pop(from_phone, None)
            return jsonify({"ok": True})
        
        # Handle help
        if text.lower() in ["help", "menu", "start", "?", "/start", "8"]:
            _send_main_menu(from_phone)
            user_states.pop(from_phone, None)
            return jsonify({"ok": True})
        
        # Default: Ask AI
        result = ask_guarded({"question": text, "account_id": account_id, "lang": "en", "channel": "whatsapp"})
        if result.get("ok"):
            answer = result.get("answer", "")
            if answer:
                send_whatsapp_text(from_phone, answer + "\n\n💡 Reply 8 for main menu.")
            else:
                send_whatsapp_text(from_phone, "I couldn't find an answer. Reply 8 for menu.")
        else:
            send_whatsapp_text(from_phone, "Sorry, I encountered an error. Reply 8 for menu.")
        
        return jsonify({"ok": True})
        
    except Exception as e:
        logging.exception(f"WA webhook error: {e}")
<<<<<<< HEAD
        send_whatsapp_text(from_phone if 'from_phone' in locals() else "Unknown", f"❌ Error: {str(e)[:100]}")
        return jsonify({"ok": True})
=======
        return jsonify({"ok": True})

>>>>>>> 5aa809ef2bfe2c6d95bde08c459f82d9b0747ce1
