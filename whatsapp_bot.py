import os
import re
import logging
import json
import random
import calendar
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
import requests
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# ============ SUPABASE CONFIGURATION ============
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = None

if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    logging.info("✅ Supabase connected successfully")

# ============ WHATSAPP CONFIGURATION ============
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "your_verify_token_here")
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
WHATSAPP_API_URL = "https://graph.facebook.com/v18.0"

# ============ USER SESSIONS ============
user_sessions = {}

# ============ WHT RATES ============
WHT_RATES = {
    "consultancy": 10, "rent": 10, "interest": 10, "dividend": 10,
    "construction": 5, "contracts": 5, "transport": 3
}

# ============ TAX CALENDAR ============
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

# ============ TAX CALCULATION FUNCTIONS ============
def calculate_paye(monthly_gross):
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
    if inclusive:
        vat = amount * 0.075 / 1.075
        exclusive = amount - vat
        total = amount
    else:
        vat = amount * 0.075
        exclusive = amount
        total = amount + vat
    return {"amount": amount, "vat": round(vat), "exclusive": round(exclusive), "total": round(total)}

def calculate_wht(amount, trans_type):
    rate = WHT_RATES.get(trans_type, 10)
    wht = amount * rate / 100
    return {"amount": amount, "rate": rate, "wht": round(wht), "net": round(amount - wht)}

# ============ SUBSCRIPTION PLANS FROM DATABASE ============
def get_plans_list_menu() -> str:
    """Fetch plans directly from database and format as numbered menu"""
    try:
        result = supabase.table("plans").select("*").eq("active", True).execute()
        plans = result.data or []
        
        if not plans:
            return "📋 *Subscription Plans*\n\nNo plans available at the moment. Please check back later."
        
        # Sort by price
        plans.sort(key=lambda x: x.get("price", 0))
        
        menu_lines = ["📋 *AVAILABLE SUBSCRIPTION PLANS*\n"]
        
        for idx, plan in enumerate(plans, 1):
            name = plan.get("name", "Unknown")
            price = plan.get("price", 0)
            credits = plan.get("ai_credits_total", 0)
            
            # Determine billing cycle from plan_code
            plan_code = plan.get("plan_code", "")
            if "yearly" in plan_code:
                billing = "year"
            elif "quarterly" in plan_code:
                billing = "quarter"
            else:
                billing = "month"
            
            # Use numbers with emojis
            number_emoji = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
            emoji = number_emoji[idx-1] if idx <= len(number_emoji) else f"{idx}️⃣"
            
            menu_lines.append(f"{emoji} - *{name}* - ₦{price:,}/{billing} - {credits} AI credits")
        
        menu_lines.append("\n💡 Send the plan number to subscribe (e.g., '1')")
        menu_lines.append("0 - Cancel | # - Main Menu")
        
        return "\n".join(menu_lines)
    except Exception as e:
        logging.error(f"Error fetching plans: {e}")
        return "📋 *Subscription Plans*\n\nPlease visit www.naijataxguides.com/plans to view plans."

# ============ WHATSAPP MENUS ============
def send_whatsapp_text(to_phone, text):
    try:
        url = f"{WHATSAPP_API_URL}/{PHONE_NUMBER_ID}/messages"
        headers = {"Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}", "Content-Type": "application/json"}
        payload = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to_phone, "type": "text", "text": {"body": text}}
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        if response.status_code == 200:
            logging.info(f"Message sent to {to_phone}")
            return True
        else:
            logging.error(f"Failed to send: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        logging.error(f"Send error: {e}")
        return False

def send_main_menu(phone):
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
        "9 - Resume\n\n"
        "Or type your tax question directly!"
    )
    send_whatsapp_text(phone, menu)

def send_tax_menu(phone):
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

def send_calculator_menu(phone):
    menu = (
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
    send_whatsapp_text(phone, menu)

def send_paye_calculator(phone):
    send_whatsapp_text(phone, "💰 *PAYE Calculator*\n\nEnter your monthly salary (e.g., 500000):\n\n💡 * - Back | # - Save & Menu | 0 - Cancel")

def send_cit_calculator(phone):
    send_whatsapp_text(phone, "🏢 *CIT Calculator*\n\nEnter your annual turnover (e.g., 50000000):\n\n💡 * - Back | # - Save & Menu | 0 - Cancel")

def send_vat_calculator(phone):
    send_whatsapp_text(phone, "🧾 *VAT Calculator*\n\n1️⃣ - Add VAT (exclusive amount)\n2️⃣ - Extract VAT (inclusive amount)\n\n💡 * - Back | # - Save & Menu | 0 - Cancel")

def send_wht_calculator(phone):
    send_whatsapp_text(phone, "📊 *WHT Calculator*\n\nEnter amount and type, e.g.: 500000 consultancy\n\nTypes: consultancy, rent, interest, construction, transport\n\n💡 * - Back | # - Save & Menu | 0 - Cancel")

def send_help(phone):
    help_text = (
        "*❓ Help - How to Use This Bot*\n\n"
        "*Main Menu:*\n"
        "• Send 1-8 to navigate the menu\n"
        "• Send # to save and return to main menu\n"
        "• Send * to go back\n"
        "• Send 0 to cancel\n"
        "• Send 9 to resume\n\n"
        "*Quick Commands:*\n"
        "• Send a number - Calculate PAYE\n\n"
        "*Support:*\n"
        "• Send a question directly for AI tax assistance\n"
        "• Or select Option 1 from main menu"
    )
    send_whatsapp_text(phone, help_text)

# ============ DATABASE FUNCTIONS ============
def get_or_create_user(user_id, name=None):
    if not supabase:
        return None
    try:
        response = supabase.table("bot_users").select("*").eq("platform", "whatsapp").eq("user_id", str(user_id)).execute()
        if response.data:
            return response.data[0]
        else:
            new_user = {
                "platform": "whatsapp",
                "user_id": str(user_id),
                "name": name,
                "created_at": datetime.now().isoformat(),
                "total_calculations": 0,
                "is_active": True
            }
            result = supabase.table("bot_users").insert(new_user).execute()
            logging.info(f"✅ New user created: whatsapp/{user_id}")
            return result.data[0] if result.data else None
    except Exception as e:
        logging.error(f"Database user error: {e}")
        return None

def log_calculation(user_id, calc_type, input_data, result_data):
    if not supabase:
        return False
    try:
        record = {
            "user_id": str(user_id),
            "calculation_type": calc_type,
            "input_data": json.dumps(input_data),
            "result_data": json.dumps(result_data),
            "created_at": datetime.now().isoformat()
        }
        supabase.table("bot_calculations").insert(record).execute()
        supabase.table("bot_users").update({
            "total_calculations": supabase.raw("total_calculations + 1"),
            "last_active": datetime.now().isoformat()
        }).eq("user_id", str(user_id)).execute()
        return True
    except Exception as e:
        logging.error(f"Log calculation error: {e}")
        return False

def get_tax_calendar():
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
    
    return msg

def get_comparison_result(salaries):
    if len(salaries) < 2:
        return "Need at least 2 salaries to compare."
    msg = "*📊 SALARY COMPARISON RESULT*\n\n"
    for i, s in enumerate(salaries, 1):
        msg += f"{i}. ₦{s['gross']:,.0f} → ₦{s['net']:,.0f} net (Tax: ₦{s['tax']:,.0f}, Rate: {s['rate']}%)\n"
    best = max(salaries, key=lambda x: x['net'])
    msg += f"\n✅ *Best take-home:* ₦{best['gross']:,.0f} → ₦{best['net']:,.0f}"
    return msg

# ============ MAIN WEBHOOK ============
@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "healthy", "whatsapp": True, "timestamp": datetime.now().isoformat()})

@app.route('/api/whatsapp/webhook', methods=['GET', 'POST'])
def whatsapp_webhook():
    # Verification
    if request.method == 'GET':
        mode = request.args.get('hub.mode')
        token = request.args.get('hub.verify_token')
        challenge = request.args.get('hub.challenge')
        if mode == 'subscribe' and token and WHATSAPP_VERIFY_TOKEN and token == WHATSAPP_VERIFY_TOKEN:
            return challenge, 200
        return "Verification failed", 403
    
    # Handle messages
    try:
        body = request.get_json()
        if not body:
            return jsonify({"status": "ok"}), 200
        
        entry = body.get('entry', [{}])[0]
        changes = entry.get('changes', [{}])[0]
        value = changes.get('value', {})
        messages = value.get('messages', [])
        
        if not messages:
            return jsonify({"status": "ok"}), 200
        
        for message in messages:
            from_number = message.get('from')
            msg_type = message.get('type')
            
            if msg_type == 'text':
                text = message.get('text', {}).get('body', '').strip()
                logging.info(f"WhatsApp {from_number}: {text}")
                
                get_or_create_user(from_number)
                user_state = user_sessions.get(from_number, {})
                current_menu = user_state.get('menu', 'main')
                calc_type = user_state.get('calc_type')
                compare_salaries = user_state.get('compare_salaries', [])
                
                # Global commands
                if text == '#':
                    user_sessions.pop(from_number, None)
                    send_main_menu(from_number)
                    return jsonify({"status": "ok"}), 200
                
                if text == '0':
                    user_sessions.pop(from_number, None)
                    send_whatsapp_text(from_number, "❌ Cancelled. Send 8 for main menu.")
                    return jsonify({"status": "ok"}), 200
                
                if text == '*':
                    if current_menu == 'calculator':
                        send_calculator_menu(from_number)
                        user_sessions[from_number] = {'menu': 'calculator'}
                    elif current_menu == 'tax_menu':
                        send_main_menu(from_number)
                        user_sessions.pop(from_number, None)
                    else:
                        send_main_menu(from_number)
                        user_sessions.pop(from_number, None)
                    return jsonify({"status": "ok"}), 200
                
                # Calculator inputs
                if calc_type == 'paye':
                    try:
                        amount = float(text.replace(',', ''))
                        if amount > 0:
                            data = calculate_paye(amount)
                            result = f"""*PAYE CALCULATION RESULT*

Gross: ₦{data['gross']:,.0f}
Pension: ₦{data['pension']:,.0f}
NHF: ₦{data['nhf']:,.0f}
Tax: ₦{data['tax']:,.0f}
Net: *₦{data['net']:,.0f}*
Rate: {data['rate']}%"""
                            send_whatsapp_text(from_number, result)
                            log_calculation(from_number, "paye", {"salary": amount}, data)
                            user_sessions.pop(from_number, None)
                            send_calculator_menu(from_number)
                        else:
                            send_whatsapp_text(from_number, "Please enter a valid positive amount.")
                    except:
                        send_whatsapp_text(from_number, "Please enter a valid number (e.g., 500000)")
                    return jsonify({"status": "ok"}), 200
                
                if calc_type == 'cit':
                    try:
                        amount = float(text.replace(',', ''))
                        if amount > 0:
                            data = calculate_cit(amount)
                            result = f"""*CIT CALCULATION RESULT*

Turnover: ₦{data['turnover']:,.0f}
Profit: ₦{data['profit']:,.0f}
Size: {data['size']}
Tax: *₦{data['total']:,.0f}*"""
                            send_whatsapp_text(from_number, result)
                            log_calculation(from_number, "cit", {"turnover": amount}, data)
                            user_sessions.pop(from_number, None)
                            send_calculator_menu(from_number)
                        else:
                            send_whatsapp_text(from_number, "Please enter a valid positive amount.")
                    except:
                        send_whatsapp_text(from_number, "Please enter a valid number (e.g., 50000000)")
                    return jsonify({"status": "ok"}), 200
                
                if calc_type == 'salary_compare':
                    if text.lower() == 'done':
                        if len(compare_salaries) >= 2:
                            result = get_comparison_result(compare_salaries)
                            send_whatsapp_text(from_number, result)
                            log_calculation(from_number, "compare", {"count": len(compare_salaries)}, {})
                        else:
                            send_whatsapp_text(from_number, "Need at least 2 salaries to compare.")
                        user_sessions.pop(from_number, None)
                        send_calculator_menu(from_number)
                    else:
                        try:
                            amount = float(text.replace(',', ''))
                            if amount > 0:
                                data = calculate_paye(amount)
                                compare_salaries.append(data)
                                user_sessions[from_number] = {'menu': 'calculator', 'calc_type': 'salary_compare', 'compare_salaries': compare_salaries}
                                if len(compare_salaries) >= 5:
                                    send_whatsapp_text(from_number, f"✅ Added ₦{amount:,.0f}\n\nYou have 5 salaries. Type 'done' to see comparison.")
                                else:
                                    send_whatsapp_text(from_number, f"✅ Added ₦{amount:,.0f}\n\nSend salary {len(compare_salaries) + 1} (or type 'done'):")
                            else:
                                send_whatsapp_text(from_number, "Please enter a valid positive amount.")
                        except:
                            send_whatsapp_text(from_number, "Please enter a valid number.")
                    return jsonify({"status": "ok"}), 200
                
                # Main menu
                if current_menu == 'main':
                    if text == '1':
                        send_whatsapp_text(from_number, "💬 Please type your tax question.\n\n💡 # - Save & Menu | 0 - Cancel")
                    elif text == '2':
                        send_whatsapp_text(from_number, "💳 *AI Credits Balance*\n\nYou have 10 credits remaining.\n\nBuy more with Option 6.")
                    elif text == '3':
                        send_whatsapp_text(from_number, "📋 *Current Plan*\n\nYou are on the Free Plan.\n\nReply 4 to view upgrade options.")
                    elif text == '4':
                        plans_menu = get_plans_list_menu()
                        send_whatsapp_text(from_number, plans_menu)
                    elif text == '5':
                        send_whatsapp_text(from_number, "🔗 *Link Website Account*\n\nVisit www.naijataxguides.com/settings to link your account.")
                    elif text == '6':
                        send_whatsapp_text(from_number, "💰 *Buy AI Credits*\n\nVisit www.naijataxguides.com/credits to purchase.")
                    elif text == '7':
                        send_tax_menu(from_number)
                        user_sessions[from_number] = {'menu': 'tax_menu'}
                    elif text == '8':
                        send_help(from_number)
                    elif text.isdigit() and len(text) >= 5:
                        try:
                            amount = float(text.replace(',', ''))
                            if amount > 0:
                                data = calculate_paye(amount)
                                result = f"""*PAYE CALCULATION RESULT*

Gross: ₦{data['gross']:,.0f}
Pension: ₦{data['pension']:,.0f}
NHF: ₦{data['nhf']:,.0f}
Tax: ₦{data['tax']:,.0f}
Net: *₦{data['net']:,.0f}*
Rate: {data['rate']}%"""
                                send_whatsapp_text(from_number, result)
                                log_calculation(from_number, "paye", {"salary": amount}, data)
                            else:
                                send_whatsapp_text(from_number, "Send 8 for main menu.")
                        except:
                            send_whatsapp_text(from_number, "Send 8 for main menu.")
                    else:
                        send_whatsapp_text(from_number, "Send 8 for main menu.")
                
                # Tax menu
                elif current_menu == 'tax_menu':
                    if text == '1':
                        send_calculator_menu(from_number)
                        user_sessions[from_number] = {'menu': 'calculator'}
                    elif text == '2':
                        send_whatsapp_text(from_number, "📋 *PAYE Tax Filing - Coming Soon*")
                    elif text == '3':
                        send_whatsapp_text(from_number, "📋 *VAT Filing - Coming Soon*")
                    elif text == '4':
                        send_whatsapp_text(from_number, "📋 *CIT Filing - Coming Soon*")
                    elif text == '5':
                        send_whatsapp_text(from_number, "📋 *Filing History*\n\nNo filings yet.")
                    elif text == '6':
                        calendar_msg = get_tax_calendar()
                        send_whatsapp_text(from_number, calendar_msg)
                    elif text == '7':
                        send_main_menu(from_number)
                        user_sessions.pop(from_number, None)
                    else:
                        send_whatsapp_text(from_number, "❌ Invalid option. Please reply with 1-7.")
                
                # Calculator menu
                elif current_menu == 'calculator':
                    if text == '1':
                        user_sessions[from_number] = {'menu': 'calculator', 'calc_type': 'paye'}
                        send_paye_calculator(from_number)
                    elif text == '2':
                        user_sessions[from_number] = {'menu': 'calculator', 'calc_type': 'cit'}
                        send_cit_calculator(from_number)
                    elif text == '3':
                        user_sessions[from_number] = {'menu': 'calculator', 'calc_type': 'vat'}
                        send_vat_calculator(from_number)
                    elif text == '4':
                        user_sessions[from_number] = {'menu': 'calculator', 'calc_type': 'wht'}
                        send_wht_calculator(from_number)
                    elif text == '5':
                        user_sessions[from_number] = {'menu': 'calculator', 'calc_type': 'salary_compare', 'compare_salaries': []}
                        send_whatsapp_text(from_number, "📊 *Salary Comparison*\n\nSend up to 5 salaries. Send 'done' when finished.\n\nSend salary 1 (e.g., 500000):")
                    elif text == '6':
                        questions = [
                            {"q": "What is the current VAT rate in Nigeria?", "opt": ["5%", "7.5%", "10%", "12.5%"], "correct": 1, "exp": "VAT is 7.5%"},
                            {"q": "By which date must PAYE be remitted?", "opt": ["7th", "14th", "21st", "30th"], "correct": 1, "exp": "PAYE due by 14th monthly"},
                            {"q": "What is the CIT rate for large companies?", "opt": ["20%", "25%", "30%", "35%"], "correct": 2, "exp": "Large companies pay 30% CIT"},
                        ]
                        q = random.choice(questions)
                        opts = "\n".join([f"{i+1}. {opt}" for i, opt in enumerate(q['opt'])])
                        user_sessions[from_number] = {'menu': 'calculator', 'calc_type': 'quiz', 'quiz_q': q}
                        send_whatsapp_text(from_number, f"📚 *TAX QUIZ*\n\n{q['q']}\n\n{opts}\n\nReply with number (1-4):")
                    elif text == '7':
                        calendar_msg = get_tax_calendar()
                        send_whatsapp_text(from_number, calendar_msg)
                    elif text == '8':
                        send_tax_menu(from_number)
                        user_sessions[from_number] = {'menu': 'tax_menu'}
                    else:
                        send_whatsapp_text(from_number, "❌ Invalid option. Please reply with 1-8.")
                
                # VAT calculator specific
                elif calc_type == 'vat':
                    if text == '1':
                        user_sessions[from_number] = {'menu': 'calculator', 'calc_type': 'vat_exclusive'}
                        send_whatsapp_text(from_number, "Enter amount (exclusive of VAT):")
                    elif text == '2':
                        user_sessions[from_number] = {'menu': 'calculator', 'calc_type': 'vat_inclusive'}
                        send_whatsapp_text(from_number, "Enter amount (inclusive of VAT):")
                    else:
                        send_whatsapp_text(from_number, "❌ Please reply with 1 or 2")
                
                # VAT amount input
                elif calc_type in ['vat_exclusive', 'vat_inclusive']:
                    try:
                        amount = float(text.replace(',', ''))
                        if amount > 0:
                            is_inclusive = (calc_type == 'vat_inclusive')
                            data = calculate_vat(amount, is_inclusive)
                            if is_inclusive:
                                result = f"""*VAT CALCULATION RESULT*

Amount (incl): ₦{data['amount']:,.0f}
VAT: ₦{data['vat']:,.0f}
Exclusive: ₦{data['exclusive']:,.0f}"""
                            else:
                                result = f"""*VAT CALCULATION RESULT*

Amount (excl): ₦{data['amount']:,.0f}
VAT: ₦{data['vat']:,.0f}
Total: ₦{data['total']:,.0f}"""
                            send_whatsapp_text(from_number, result)
                            log_calculation(from_number, "vat", {"amount": amount}, data)
                            user_sessions.pop(from_number, None)
                            send_calculator_menu(from_number)
                        else:
                            send_whatsapp_text(from_number, "Please enter a valid positive amount.")
                    except:
                        send_whatsapp_text(from_number, "Please enter a valid number.")
                
                # WHT calculator
                elif calc_type == 'wht':
                    parts = text.split()
                    try:
                        amount = float(parts[0].replace(',', ''))
                        trans_type = parts[1].lower() if len(parts) > 1 else "consultancy"
                        if trans_type in WHT_RATES:
                            data = calculate_wht(amount, trans_type)
                            result = f"""*WHT CALCULATION RESULT*

Amount: ₦{data['amount']:,.0f}
Rate: {data['rate']}%
WHT: *₦{data['wht']:,.0f}*
Net: ₦{data['net']:,.0f}"""
                            send_whatsapp_text(from_number, result)
                            log_calculation(from_number, "wht", {"amount": amount, "type": trans_type}, data)
                            user_sessions.pop(from_number, None)
                            send_calculator_menu(from_number)
                        else:
                            send_whatsapp_text(from_number, "❌ Invalid type. Types: consultancy, rent, interest, construction, transport")
                    except:
                        send_whatsapp_text(from_number, "❌ Please enter amount and type e.g., 500000 consultancy")
                
                # Quiz answer
                elif calc_type == 'quiz':
                    if text in ['1', '2', '3', '4']:
                        q = user_state.get('quiz_q')
                        selected = int(text) - 1
                        if selected == q.get('correct'):
                            send_whatsapp_text(from_number, f"✅ *Correct!* {q.get('exp', 'Well done!')}")
                        else:
                            correct_opt = q.get('opt', [])[q.get('correct', 0)]
                            send_whatsapp_text(from_number, f"❌ *Incorrect!* The correct answer is {correct_opt}.\n\n{q.get('exp', '')}")
                        user_sessions.pop(from_number, None)
                        send_calculator_menu(from_number)
                    else:
                        send_whatsapp_text(from_number, "❌ Please reply with 1, 2, 3, or 4")
                
                else:
                    send_main_menu(from_number)
        
        return jsonify({"status": "ok"}), 200
        
    except Exception as e:
        logging.exception(f"Webhook error: {e}")
        return jsonify({"status": "error"}), 500

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8000))
    app.run(host='0.0.0.0', port=port)