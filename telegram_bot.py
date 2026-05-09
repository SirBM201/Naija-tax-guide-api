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

# ============ TELEGRAM CONFIGURATION ============
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
TELEGRAM_ENABLED = bool(TELEGRAM_TOKEN)

# ============ CRON JOB TEST USERS ============
TEST_TELEGRAM_CHAT_ID = os.getenv("TEST_TELEGRAM_CHAT_ID")

# ============ USER SESSIONS ============
user_sessions = {}
user_calc_sessions = {}
user_ai_sessions = {}

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
    9: {14: "PAYE Remittance (Aug)", 21: "VAT Filing (Aug)"},
    10: {14: "PAYE Remittance (Sep)", 21: "VAT Filing (Sep)", 31: "Q3 CIT Filing"},
    11: {14: "PAYE Remittance (Oct)", 21: "VAT Filing (Oct)"},
    12: {14: "PAYE Remittance (Nov)", 21: "VAT Filing (Nov)", 31: "Year-end Planning"},
}

# ============ CALCULATION FUNCTIONS ============
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
        "pension": round(pension),
        "nhf": round(nhf),
        "tax": round(monthly_tax),
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

def get_upcoming_deadlines(days=30):
    today = datetime.now()
    upcoming = []
    for month in range(today.month, today.month + 2):
        m = ((month - 1) % 12) + 1
        year = today.year + (month - 1) // 12
        for day, name in TAX_CALENDAR.get(m, {}).items():
            d = datetime(year, m, day)
            if d >= today:
                diff = (d - today).days
                if diff <= days:
                    upcoming.append({"name": name, "days": diff, "date": d})
    return sorted(upcoming, key=lambda x: x["days"])[:10]

# ============ DATABASE FUNCTIONS ============
def get_or_create_user(platform, user_id, name=None):
    if not supabase:
        return None
    try:
        response = supabase.table("bot_users").select("*").eq("platform", platform).eq("user_id", str(user_id)).execute()
        if response.data:
            return response.data[0]
        else:
            new_user = {
                "platform": platform,
                "user_id": str(user_id),
                "name": name,
                "created_at": datetime.now().isoformat(),
                "total_calculations": 0,
                "is_active": True
            }
            result = supabase.table("bot_users").insert(new_user).execute()
            logging.info(f"✅ New user created: {platform}/{user_id}")
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

# ============ MENU DEFINITIONS ============
MAIN_MENU = """
🇳🇬 *Naija Tax Guide*

Reply with:

1️⃣ - Ask a tax question
2️⃣ - Check AI credits balance
3️⃣ - Check my subscription plan
4️⃣ - View subscription plans
5️⃣ - Link to website account
6️⃣ - Buy AI credits
7️⃣ - Tax filing & management
8️⃣ - Help / Menu

---
*Global commands:*
# - Save & Menu | * - Back | 0 - Cancel | 9 - Resume

*Calculator:*
Type 'calc paye 500000' to calculate tax
"""

TAX_FILING_MENU = """
📋 *Tax Filing & Management*

Select an option:

1️⃣ - PAYE Tax Calculator
2️⃣ - Company Income Tax (CIT)
3️⃣ - VAT Calculator
4️⃣ - Withholding Tax (WHT)
5️⃣ - Salary Comparison
6️⃣ - Tax Quiz
7️⃣ - Tax Calendar & Deadlines
8️⃣ - Filing Guides & Checklists
9️⃣ - Back to Main Menu
"""

CALCULATOR_MENU = """
🧮 *Tax Calculator*

1️⃣ - PAYE
2️⃣ - CIT
3️⃣ - VAT
4️⃣ - WHT
5️⃣ - Back
"""

HELP_MENU = """
❓ *Help - How to Use This Bot*

*Main Menu:*
• Send 1-8 to navigate the menu
• Send # to save and return to main menu
• Send * to go back
• Send 0 to cancel
• Send 9 to resume

*Quick Commands:*
• `calc paye 500000` - Calculate PAYE
• `calc cit 50000000` - Calculate CIT
• `calc vat 100000` - Calculate VAT
• `calc wht 500000 consultancy` - Calculate WHT

*Support:*
• Send a question directly for AI tax assistance
"""

# ============ SEND MESSAGE ============
def send_telegram_message(chat_id, text):
    if not TELEGRAM_TOKEN:
        return False
    try:
        url = f"{TELEGRAM_API_URL}/sendMessage"
        requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}, timeout=10)
        return True
    except Exception as e:
        logging.error(f"Send error: {e}")
        return False

# ============ COMMAND PROCESSING ============
def process_command(chat_id, text, user_name="User"):
    get_or_create_user("telegram", chat_id, user_name)
    
    # Global commands
    if text == '#':
        user_sessions.pop(chat_id, None)
        user_calc_sessions.pop(chat_id, None)
        user_ai_sessions.pop(chat_id, None)
        send_telegram_message(chat_id, MAIN_MENU)
        return True
    
    if text == '*':
        if chat_id in user_sessions:
            current_menu = user_sessions.get(chat_id, {}).get('menu', 'main')
            if current_menu == 'tax_filing':
                send_telegram_message(chat_id, MAIN_MENU)
                user_sessions.pop(chat_id, None)
            elif current_menu == 'calculator':
                send_telegram_message(chat_id, TAX_FILING_MENU)
                user_sessions[chat_id] = {'menu': 'tax_filing'}
            else:
                send_telegram_message(chat_id, MAIN_MENU)
                user_sessions.pop(chat_id, None)
        else:
            send_telegram_message(chat_id, MAIN_MENU)
        return True
    
    if text == '0':
        user_sessions.pop(chat_id, None)
        user_calc_sessions.pop(chat_id, None)
        user_ai_sessions.pop(chat_id, None)
        send_telegram_message(chat_id, "❌ Cancelled. Send # for main menu.")
        return True
    
    # Quick calc command
    calc_match = re.match(r'^calc\s+(paye|cit|vat|vatin|wht)\s+([\d,]+)(?:\s+(\w+))?', text.lower())
    if calc_match:
        calc_type = calc_match.group(1)
        amount = float(calc_match.group(2).replace(',', ''))
        calc_param = calc_match.group(3) if len(calc_match.groups()) > 2 else None
        
        if calc_type == 'paye':
            data = calculate_paye(amount)
            result = f"""*PAYE SUMMARY*\n\nGross: ₦{data['gross']:,.0f}\nPension: ₦{data['pension']:,.0f}\nNHF: ₦{data['nhf']:,.0f}\nTax: ₦{data['tax']:,.0f}\nNet: *₦{data['net']:,.0f}*\nRate: {data['rate']}%"""
            send_telegram_message(chat_id, result)
            log_calculation(chat_id, "paye", {"salary": amount}, data)
        elif calc_type == 'cit':
            data = calculate_cit(amount)
            result = f"""*CIT SUMMARY*\n\nTurnover: ₦{data['turnover']:,.0f}\nProfit: ₦{data['profit']:,.0f}\nSize: {data['size']}\nTax: *₦{data['total']:,.0f}*"""
            send_telegram_message(chat_id, result)
            log_calculation(chat_id, "cit", {"turnover": amount}, data)
        elif calc_type == 'vat':
            data = calculate_vat(amount, False)
            result = f"""*VAT (7.5%)*\n\nAmount (excl): ₦{data['amount']:,.0f}\nVAT: ₦{data['vat']:,.0f}\nTotal: ₦{data['total']:,.0f}"""
            send_telegram_message(chat_id, result)
            log_calculation(chat_id, "vat", {"amount": amount}, data)
        elif calc_type == 'vatin':
            data = calculate_vat(amount, True)
            result = f"""*VAT (7.5%)*\n\nAmount (incl): ₦{data['amount']:,.0f}\nVAT: ₦{data['vat']:,.0f}\nExclusive: ₦{data['exclusive']:,.0f}"""
            send_telegram_message(chat_id, result)
            log_calculation(chat_id, "vat", {"amount": amount}, data)
        elif calc_type == 'wht':
            trans_type = calc_param if calc_param else "consultancy"
            data = calculate_wht(amount, trans_type)
            result = f"""*WITHHOLDING TAX*\n\nAmount: ₦{data['amount']:,.0f}\nRate: {data['rate']}%\nWHT: *₦{data['wht']:,.0f}*\nNet: ₦{data['net']:,.0f}"""
            send_telegram_message(chat_id, result)
            log_calculation(chat_id, "wht", {"amount": amount, "type": trans_type}, data)
        return True
    
    # AI question mode
    if chat_id in user_ai_sessions and user_ai_sessions[chat_id].get('active'):
        send_telegram_message(chat_id, "🤖 *AI Response*\n\nThank you for your question. This feature will integrate with your website's AI.")
        user_ai_sessions.pop(chat_id, None)
        send_telegram_message(chat_id, MAIN_MENU)
        return True
    
    # Calculator input mode
    if chat_id in user_calc_sessions:
        calc_context = user_calc_sessions[chat_id].get('type')
        
        if calc_context == 'paye':
            try:
                salary = float(text.replace(',', ''))
                if salary > 0:
                    data = calculate_paye(salary)
                    result = f"""*PAYE SUMMARY*\n\nGross: ₦{data['gross']:,.0f}\nPension: ₦{data['pension']:,.0f}\nNHF: ₦{data['nhf']:,.0f}\nTax: ₦{data['tax']:,.0f}\nNet: *₦{data['net']:,.0f}*\nRate: {data['rate']}%"""
                    send_telegram_message(chat_id, result)
                    log_calculation(chat_id, "paye", {"salary": salary}, data)
                    send_telegram_message(chat_id, TAX_FILING_MENU)
                    user_calc_sessions.pop(chat_id, None)
                    user_sessions[chat_id] = {'menu': 'tax_filing'}
                else:
                    send_telegram_message(chat_id, "Please enter a valid positive amount.")
            except:
                send_telegram_message(chat_id, "Please enter a valid number (e.g., 500000)")
            return True
        
        elif calc_context == 'cit':
            try:
                turnover = float(text.replace(',', ''))
                if turnover > 0:
                    data = calculate_cit(turnover)
                    result = f"""*CIT SUMMARY*\n\nTurnover: ₦{data['turnover']:,.0f}\nProfit: ₦{data['profit']:,.0f}\nSize: {data['size']}\nTax: *₦{data['total']:,.0f}*"""
                    send_telegram_message(chat_id, result)
                    log_calculation(chat_id, "cit", {"turnover": turnover}, data)
                    send_telegram_message(chat_id, TAX_FILING_MENU)
                    user_calc_sessions.pop(chat_id, None)
                    user_sessions[chat_id] = {'menu': 'tax_filing'}
                else:
                    send_telegram_message(chat_id, "Please enter a valid positive amount.")
            except:
                send_telegram_message(chat_id, "Please enter a valid number (e.g., 50000000)")
            return True
    
    # Menu navigation
    if chat_id not in user_sessions:
        user_sessions[chat_id] = {'menu': 'main'}
    
    current_menu = user_sessions[chat_id].get('menu', 'main')
    
    # MAIN MENU
    if current_menu == 'main':
        if text == '1':
            user_ai_sessions[chat_id] = {'active': True}
            send_telegram_message(chat_id, "🤖 *Ask AI Tax Assistant*\n\nType your tax question below:")
            return True
        elif text == '2':
            send_telegram_message(chat_id, "💳 *AI Credits Balance*\n\nYou have 10 credits remaining.\n\nBuy more with Option 6.")
            return True
        elif text == '3':
            send_telegram_message(chat_id, "📋 *Current Plan*\n\nYou are on the Free Plan.\n\nVisit www.naijataxguides.com/plans to upgrade.")
            return True
        elif text == '4':
            send_telegram_message(chat_id, "📋 *Subscription Plans*\n\nVisit www.naijataxguides.com/plans to view all subscription plans.")
            return True
        elif text == '5':
            send_telegram_message(chat_id, "🔗 *Link Website Account*\n\nVisit www.naijataxguides.com/settings to link your account.")
            return True
        elif text == '6':
            send_telegram_message(chat_id, "💰 *Buy AI Credits*\n\nVisit www.naijataxguides.com/credits to purchase AI credits.")
            return True
        elif text == '7':
            user_sessions[chat_id] = {'menu': 'tax_filing'}
            send_telegram_message(chat_id, TAX_FILING_MENU)
            return True
        elif text == '8':
            send_telegram_message(chat_id, HELP_MENU)
            return True
    
    # TAX FILING MENU
    elif current_menu == 'tax_filing':
        if text == '1':
            user_sessions[chat_id] = {'menu': 'calculator'}
            send_telegram_message(chat_id, CALCULATOR_MENU)
            return True
        elif text == '2':
            user_sessions[chat_id] = {'menu': 'calculator'}
            send_telegram_message(chat_id, CALCULATOR_MENU)
            return True
        elif text == '3':
            user_sessions[chat_id] = {'menu': 'calculator'}
            send_telegram_message(chat_id, CALCULATOR_MENU)
            return True
        elif text == '4':
            user_sessions[chat_id] = {'menu': 'calculator'}
            send_telegram_message(chat_id, CALCULATOR_MENU)
            return True
        elif text == '5':
            send_telegram_message(chat_id, "📊 *Salary Comparison*\n\nSend up to 5 salaries. Send 'done' when finished.\n\nSend salary 1:")
            user_calc_sessions[chat_id] = {'type': 'compare', 'salaries': []}
            return True
        elif text == '6':
            questions = [
                {"q": "What is the current VAT rate in Nigeria?", "opt": ["5%", "7.5%", "10%", "12.5%"], "correct": 1},
                {"q": "By which date must PAYE be remitted?", "opt": ["7th", "14th", "21st", "30th"], "correct": 1},
                {"q": "What is the CIT rate for large companies?", "opt": ["20%", "25%", "30%", "35%"], "correct": 2},
            ]
            q = random.choice(questions)
            opts = "\n".join([f"{i+1}. {opt}" for i, opt in enumerate(q['opt'])])
            send_telegram_message(chat_id, f"📚 *TAX QUIZ*\n\n{q['q']}\n\n{opts}\n\nReply with number (1-4):")
            user_calc_sessions[chat_id] = {'type': 'quiz', 'correct': q['correct']}
            return True
        elif text == '7':
            today = datetime.now()
            cal = calendar.monthcalendar(today.year, today.month)
            month = today.strftime("%B")
            msg = f"*📅 {month} {today.year} - Tax Calendar*\n\n"
            msg += "Mon Tue Wed Thu Fri Sat Sun\n"
            for week in cal:
                for day in week:
                    if day == 0:
                        msg += "    "
                    else:
                        if day in TAX_CALENDAR.get(today.month, {}):
                            msg += f"*{day:2d}* "
                        else:
                            msg += f"{day:2d} "
                msg += "\n"
            send_telegram_message(chat_id, msg)
            return True
        elif text == '8':
            guides = """
📋 *Filing Guides*

*PAYE Guide:*
1. Calculate PAYE per employee
2. Deduct PAYE, Pension (8%), NHF (2.5%)
3. File Schedule 6
4. Remit by 14th monthly

*CIT Guide:*
• Small (< ₦25M): File nil returns
• Medium (₦25M-₦100M): 20% CIT
• Large (> ₦100M): 30% CIT

*VAT Guide:*
1. Track Output VAT and Input VAT
2. Output - Input = Payable
3. File Form 002 by 21st monthly

*WHT Guide:*
1. Deduct WHT from eligible payments
2. File Form 1 by 21st monthly
3. Issue credit notes to vendors
"""
            send_telegram_message(chat_id, guides)
            return True
        elif text == '9':
            send_telegram_message(chat_id, MAIN_MENU)
            user_sessions.pop(chat_id, None)
            return True
    
    # CALCULATOR MENU
    elif current_menu == 'calculator':
        if text == '1':
            user_calc_sessions[chat_id] = {'type': 'paye'}
            send_telegram_message(chat_id, "💰 *PAYE Calculator*\n\nEnter your monthly salary (e.g., 500000):")
            return True
        elif text == '2':
            user_calc_sessions[chat_id] = {'type': 'cit'}
            send_telegram_message(chat_id, "🏢 *CIT Calculator*\n\nEnter your annual turnover (e.g., 50000000):")
            return True
        elif text == '3':
            user_calc_sessions[chat_id] = {'type': 'vat_exclusive'}
            send_telegram_message(chat_id, "🧾 *VAT Calculator*\n\n1 - Add VAT\n2 - Extract VAT\n\nSelect 1 or 2:")
            return True
        elif text == '4':
            user_calc_sessions[chat_id] = {'type': 'wht', 'step': 'amount'}
            send_telegram_message(chat_id, "📊 *WHT Calculator*\n\nEnter amount (e.g., 500000):")
            return True
        elif text == '5':
            send_telegram_message(chat_id, TAX_FILING_MENU)
            user_sessions[chat_id] = {'menu': 'tax_filing'}
            return True
    
    # Salary comparison
    if chat_id in user_calc_sessions and user_calc_sessions[chat_id].get('type') == 'compare':
        if text.lower() == 'done':
            salaries = user_calc_sessions[chat_id].get('salaries', [])
            if len(salaries) >= 2:
                msg = "*📊 SALARY COMPARISON*\n\n"
                for i, s in enumerate(salaries, 1):
                    msg += f"{i}. ₦{s['gross']:,.0f} → ₦{s['net']:,.0f} net (Tax: ₦{s['tax']:,.0f})\n"
                best = max(salaries, key=lambda x: x['net'])
                msg += f"\n✅ *Best net:* ₦{best['gross']:,.0f} → ₦{best['net']:,.0f}"
                send_telegram_message(chat_id, msg)
                log_calculation(chat_id, "compare", {"salaries": len(salaries)}, {"best": best['gross']})
            else:
                send_telegram_message(chat_id, "Need at least 2 salaries to compare.")
            user_calc_sessions.pop(chat_id, None)
            send_telegram_message(chat_id, TAX_FILING_MENU)
            user_sessions[chat_id] = {'menu': 'tax_filing'}
            return True
        else:
            try:
                salary = float(text.replace(',', ''))
                if salary > 0:
                    data = calculate_paye(salary)
                    salaries = user_calc_sessions[chat_id].get('salaries', [])
                    salaries.append(data)
                    user_calc_sessions[chat_id]['salaries'] = salaries
                    total = len(salaries)
                    if total >= 5:
                        send_telegram_message(chat_id, f"✅ Added ₦{salary:,.0f}\n\nYou have 5 salaries. Type 'done' to see comparison.")
                    else:
                        send_telegram_message(chat_id, f"✅ Added ₦{salary:,.0f}\n\nSend salary {total + 1} (or type 'done'):")
                else:
                    send_telegram_message(chat_id, "Please enter a valid positive amount.")
            except:
                send_telegram_message(chat_id, "Please enter a valid number.")
            return True
    
    # Quiz answer
    if chat_id in user_calc_sessions and user_calc_sessions[chat_id].get('type') == 'quiz':
        if text in ['1', '2', '3', '4']:
            selected = int(text) - 1
            correct_idx = user_calc_sessions[chat_id].get('correct')
            if selected == correct_idx:
                send_telegram_message(chat_id, "✅ *Correct!* Well done!")
            else:
                correct_opt = ["1", "2", "3", "4"][correct_idx]
                send_telegram_message(chat_id, f"❌ *Incorrect!* The correct answer was option {correct_opt}.")
            user_calc_sessions.pop(chat_id, None)
            send_telegram_message(chat_id, TAX_FILING_MENU)
            user_sessions[chat_id] = {'menu': 'tax_filing'}
        else:
            send_telegram_message(chat_id, "Please reply with 1, 2, 3, or 4.")
        return True
    
    # Default: show main menu
    send_telegram_message(chat_id, MAIN_MENU)
    return True

# ============ FLASK ENDPOINTS ============

@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        "status": "healthy",
        "telegram": TELEGRAM_ENABLED,
        "timestamp": datetime.now().isoformat()
    })

@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    try:
        update = request.get_json()
        if not update or 'message' not in update:
            return jsonify({"status": "ok"}), 200
        
        msg = update['message']
        chat_id = str(msg['chat']['id'])
        user_name = msg.get('from', {}).get('first_name', 'User')
        text = msg.get('text', '').strip()
        
        logging.info(f"Telegram {chat_id}: {text}")
        process_command(chat_id, text, user_name)
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        logging.error(f"Telegram error: {e}")
        return jsonify({"status": "error"}), 500

@app.route('/api/cron/send-deadline-reminders', methods=['POST', 'GET'])
def send_deadline_reminders():
    try:
        upcoming = get_upcoming_deadlines(7)
        if upcoming:
            msg = "*📅 TAX DEADLINE REMINDERS*\n\n"
            for d in upcoming[:5]:
                if d['days'] == 0:
                    msg += f"⚠️ *TODAY:* {d['name']}\n"
                elif d['days'] == 1:
                    msg += f"🔔 *TOMORROW:* {d['name']}\n"
                else:
                    msg += f"📌 {d['name']} - {d['days']} days\n"
            
            if TEST_TELEGRAM_CHAT_ID and TELEGRAM_ENABLED:
                send_telegram_message(TEST_TELEGRAM_CHAT_ID, msg)
        
        return jsonify({"status": "success", "deadlines": len(upcoming)}), 200
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route('/api/cron/daily-tip', methods=['POST', 'GET'])
def daily_tip():
    try:
        tips = [
            "💡 Use 'calc paye 500000' to calculate PAYE tax quickly!",
            "💡 Type # to return to main menu from anywhere!",
            "💡 Send * to go back to previous menu!",
            "💡 Need help? Select Option 8 from main menu!",
            "💡 Use Option 7 for all tax calculations and filing!",
        ]
        tip = random.choice(tips)
        
        if TEST_TELEGRAM_CHAT_ID and TELEGRAM_ENABLED:
            send_telegram_message(TEST_TELEGRAM_CHAT_ID, tip)
        
        return jsonify({"status": "success"}), 200
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8000))
    app.run(host='0.0.0.0', port=port)