import os
import re
import logging
import json
import random
import datetime
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
else:
    logging.warning("⚠️ Supabase not configured - database features disabled")

# ============ TELEGRAM CONFIGURATION ============
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

if TELEGRAM_TOKEN:
    logging.info(f"✅ TELEGRAM_TOKEN loaded. Length: {len(TELEGRAM_TOKEN)}")
else:
    logging.error("❌ TELEGRAM_TOKEN NOT FOUND!")

# ============ WHATSAPP CONFIGURATION ============
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "your_verify_token_here")
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
WHATSAPP_API_URL = "https://graph.facebook.com/v18.0"

if WHATSAPP_ACCESS_TOKEN:
    logging.info(f"✅ WHATSAPP_ACCESS_TOKEN loaded. Length: {len(WHATSAPP_ACCESS_TOKEN)}")
else:
    logging.warning("⚠️ WHATSAPP_ACCESS_TOKEN not configured")

# ============ CRON JOB TEST USERS ============
TEST_TELEGRAM_CHAT_ID = os.getenv("TEST_TELEGRAM_CHAT_ID")
TEST_WHATSAPP_NUMBER = os.getenv("TEST_WHATSAPP_NUMBER")

# ============ DATABASE FUNCTIONS ============
def get_or_create_user(platform, user_id, name=None):
    """Get existing user or create new one in database"""
    if not supabase:
        return None
    
    try:
        # Check if user exists
        response = supabase.table("users").select("*").eq("platform", platform).eq("user_id", str(user_id)).execute()
        
        if response.data:
            return response.data[0]
        else:
            # Create new user
            new_user = {
                "platform": platform,
                "user_id": str(user_id),
                "name": name,
                "created_at": datetime.now().isoformat(),
                "total_calculations": 0,
                "is_active": True
            }
            result = supabase.table("users").insert(new_user).execute()
            logging.info(f"New user created: {platform}/{user_id}")
            return result.data[0] if result.data else None
    except Exception as e:
        logging.error(f"Database user error: {e}")
        return None

def log_calculation(user_id, calculation_type, input_data, result_data):
    """Log calculation to database for history and analytics"""
    if not supabase:
        return False
    
    try:
        record = {
            "user_id": str(user_id),
            "calculation_type": calculation_type,
            "input_data": json.dumps(input_data),
            "result_data": json.dumps(result_data),
            "created_at": datetime.now().isoformat()
        }
        supabase.table("calculations").insert(record).execute()
        
        # Update user's total calculations count
        supabase.table("users").update({
            "total_calculations": supabase.raw("total_calculations + 1"),
            "last_active": datetime.now().isoformat()
        }).eq("user_id", str(user_id)).execute()
        
        return True
    except Exception as e:
        logging.error(f"Log calculation error: {e}")
        return False

def get_user_history(user_id, limit=10):
    """Get user's calculation history"""
    if not supabase:
        return None
    
    try:
        response = supabase.table("calculations").select("*").eq("user_id", str(user_id)).order("created_at", desc=True).limit(limit).execute()
        return response.data
    except Exception as e:
        logging.error(f"Get history error: {e}")
        return None

def get_user_stats(user_id):
    """Get user statistics"""
    if not supabase:
        return None
    
    try:
        # Get user info
        user = supabase.table("users").select("*").eq("user_id", str(user_id)).execute()
        
        # Get calculation counts by type
        calculations = supabase.table("calculations").select("calculation_type").eq("user_id", str(user_id)).execute()
        
        stats = {
            "total_calculations": user.data[0].get("total_calculations", 0) if user.data else 0,
            "joined_at": user.data[0].get("created_at") if user.data else None,
            "last_active": user.data[0].get("last_active") if user.data else None,
            "paye_count": 0,
            "cit_count": 0,
            "vat_count": 0
        }
        
        for calc in calculations.data:
            calc_type = calc.get("calculation_type")
            if calc_type == "paye":
                stats["paye_count"] += 1
            elif calc_type == "cit":
                stats["cit_count"] += 1
            elif calc_type == "vat":
                stats["vat_count"] += 1
        
        return stats
    except Exception as e:
        logging.error(f"Get stats error: {e}")
        return None

def save_user_preference(user_id, preference_key, preference_value):
    """Save user preference (e.g., favorite salary, notification settings)"""
    if not supabase:
        return False
    
    try:
        # Check if preference exists
        existing = supabase.table("user_preferences").select("*").eq("user_id", str(user_id)).eq("preference_key", preference_key).execute()
        
        if existing.data:
            supabase.table("user_preferences").update({"preference_value": preference_value}).eq("id", existing.data[0]["id"]).execute()
        else:
            supabase.table("user_preferences").insert({
                "user_id": str(user_id),
                "preference_key": preference_key,
                "preference_value": preference_value
            }).execute()
        return True
    except Exception as e:
        logging.error(f"Save preference error: {e}")
        return False

def get_user_preference(user_id, preference_key):
    """Get user preference"""
    if not supabase:
        return None
    
    try:
        response = supabase.table("user_preferences").select("*").eq("user_id", str(user_id)).eq("preference_key", preference_key).execute()
        return response.data[0]["preference_value"] if response.data else None
    except Exception as e:
        logging.error(f"Get preference error: {e}")
        return None

def get_all_active_users(platform=None):
    """Get all active users for broadcasting"""
    if not supabase:
        return []
    
    try:
        query = supabase.table("users").select("user_id, platform").eq("is_active", True)
        if platform:
            query = query.eq("platform", platform)
        response = query.execute()
        return response.data
    except Exception as e:
        logging.error(f"Get active users error: {e}")
        return []

def broadcast_message(users, message, platform):
    """Broadcast message to multiple users"""
    sent_count = 0
    for user in users:
        if platform == "telegram":
            if send_telegram_message(user["user_id"], message):
                sent_count += 1
        elif platform == "whatsapp":
            if send_whatsapp_message(user["user_id"], message):
                sent_count += 1
    return sent_count

# ============ TAX DEADLINES ============
TAX_DEADLINES = [
    {"name": "PAYE Monthly Remittance", "day": 14, "description": "PAYE taxes deducted in previous month must be remitted to FIRS"},
    {"name": "VAT Filing", "day": 21, "description": "Monthly VAT returns filing deadline"},
    {"name": "Company Income Tax (Q1)", "month": 4, "day": 30, "description": "First quarter CIT filing"},
    {"name": "Company Income Tax (Q2)", "month": 7, "day": 31, "description": "Second quarter CIT filing"},
    {"name": "Company Income Tax (Q3)", "month": 10, "day": 31, "description": "Third quarter CIT filing"},
    {"name": "Annual Tax Filing", "month": 3, "day": 31, "description": "Annual individual tax filing deadline"},
]

# ============ PAYE TAX CALCULATION ============
def calculate_nigerian_paye(monthly_gross):
    """Calculate Nigerian PAYE tax based on PITA"""
    annual_gross = monthly_gross * 12
    
    pension = monthly_gross * 0.08
    nhf = monthly_gross * 0.025
    
    cra_fixed = 200000
    cra_one_percent = annual_gross * 0.01
    cra_base = max(cra_fixed, cra_one_percent)
    cra_percentage = annual_gross * 0.20
    cra_total = cra_base + cra_percentage
    
    annual_pension = pension * 12
    annual_nhf = nhf * 12
    total_annual_deductions = annual_pension + annual_nhf + cra_total
    
    chargeable_income = annual_gross - total_annual_deductions
    chargeable_income = max(0, chargeable_income)
    
    if chargeable_income <= 300000:
        annual_tax = chargeable_income * 0.07
    elif chargeable_income <= 600000:
        annual_tax = 21000 + (chargeable_income - 300000) * 0.11
    elif chargeable_income <= 1100000:
        annual_tax = 54000 + (chargeable_income - 600000) * 0.15
    elif chargeable_income <= 1600000:
        annual_tax = 129000 + (chargeable_income - 1100000) * 0.19
    elif chargeable_income <= 3200000:
        annual_tax = 224000 + (chargeable_income - 1600000) * 0.21
    else:
        annual_tax = 560000 + (chargeable_income - 3200000) * 0.24
    
    if annual_tax < (annual_gross * 0.01) and annual_gross > 0:
        annual_tax = annual_gross * 0.01
    
    monthly_tax = annual_tax / 12
    effective_rate = (annual_tax / annual_gross) * 100 if annual_gross > 0 else 0
    
    return {
        "monthly_gross": monthly_gross,
        "annual_gross": annual_gross,
        "pension": round(pension, 2),
        "nhf": round(nhf, 2),
        "cra": round(cra_total / 12, 2),
        "total_monthly_deductions": round(pension + nhf, 2),
        "chargeable_income_monthly": round(chargeable_income / 12, 2),
        "chargeable_income_annual": round(chargeable_income, 2),
        "annual_tax": round(annual_tax, 2),
        "monthly_tax": round(monthly_tax, 2),
        "effective_rate": round(effective_rate, 2),
        "net_pay": round(monthly_gross - pension - nhf - monthly_tax, 2)
    }

# ============ COMPANY INCOME TAX (CIT) CALCULATION ============
def calculate_company_income_tax(annual_turnover, assessable_profit=None):
    """Calculate Nigerian Company Income Tax (CIT)"""
    
    if assessable_profit is None:
        assessable_profit = annual_turnover * 0.20
    
    if annual_turnover < 25000000:
        tax_rate = 0
        company_size = "Small (Exempt)"
    elif annual_turnover <= 100000000:
        tax_rate = 0.20
        company_size = "Medium"
    else:
        tax_rate = 0.30
        company_size = "Large"
    
    cit = assessable_profit * tax_rate
    
    minimum_tax_turnover = annual_turnover * 0.005
    minimum_tax_profit = assessable_profit * 0.005
    minimum_tax = max(minimum_tax_turnover, minimum_tax_profit, 0)
    
    education_tax = assessable_profit * 0.03
    
    it_levy = 0
    if annual_turnover > 100000000:
        it_levy = max(assessable_profit * 0.01, 0)
    
    total_tax = cit + education_tax + it_levy
    total_tax = max(total_tax, minimum_tax)
    
    return {
        "annual_turnover": round(annual_turnover, 2),
        "assessable_profit": round(assessable_profit, 2),
        "company_size": company_size,
        "tax_rate": tax_rate,
        "cit": round(cit, 2),
        "education_tax": round(education_tax, 2),
        "it_levy": round(it_levy, 2),
        "minimum_tax": round(minimum_tax, 2),
        "total_tax": round(total_tax, 2),
        "effective_rate": round((total_tax / annual_turnover) * 100, 2)
    }

# ============ VAT CALCULATION ============
def calculate_vat(amount, is_inclusive=False):
    """Calculate Nigerian VAT (7.5%)"""
    vat_rate = 0.075
    
    if is_inclusive:
        vat = amount * (vat_rate / (1 + vat_rate))
        exclusive_amount = amount - vat
    else:
        vat = amount * vat_rate
        exclusive_amount = amount
    
    total_with_vat = exclusive_amount + vat
    
    return {
        "original_amount": round(amount, 2),
        "vat_rate": f"{vat_rate * 100}%",
        "vat": round(vat, 2),
        "exclusive_amount": round(exclusive_amount, 2),
        "total_with_vat": round(total_with_vat, 2),
        "is_inclusive": is_inclusive
    }

def calculate_vat_liability(input_vat, output_vat):
    """Calculate VAT payable/refundable for businesses"""
    liability = output_vat - input_vat
    status = "Payable to FIRS" if liability > 0 else "Refundable" if liability < 0 else "No liability"
    
    return {
        "input_vat": round(input_vat, 2),
        "output_vat": round(output_vat, 2),
        "net_liability": round(liability, 2),
        "status": status
    }

# ============ FORMATTING FUNCTIONS ============
def format_paye_summary(data):
    return f"""
🇳🇬 *NIGERIA PAYE TAX SUMMARY*

📊 *Monthly Gross:* ₦{data['monthly_gross']:,.2f}
📈 *Annual Gross:* ₦{data['annual_gross']:,.2f}

📋 *Monthly Deductions:*
• Pension (8%): ₦{data['pension']:,.2f}
• NHF (2.5%): ₦{data['nhf']:,.2f}
• CRA Relief: ₦{data['cra']:,.2f}
• *Total:* ₦{data['total_monthly_deductions']:,.2f}

🎯 *Chargeable Income:*
• Monthly: ₦{data['chargeable_income_monthly']:,.2f}
• Annual: ₦{data['chargeable_income_annual']:,.2f}

🧾 *Tax Due:*
• *Annual Tax:* ₦{data['annual_tax']:,.2f}
• *Monthly Tax:* ₦{data['monthly_tax']:,.2f}
• *Effective Rate:* {data['effective_rate']}%

💵 *Net Monthly Take-home:* ₦{data['net_pay']:,.2f}
"""

def format_cit_summary(data):
    tax_rate_display = f"{data['tax_rate'] * 100:.0f}%" if data['tax_rate'] > 0 else "Exempt (0%)"
    
    return f"""
🏢 *NIGERIA COMPANY INCOME TAX (CIT)*

📊 *Annual Turnover:* ₦{data['annual_turnover']:,.2f}
📈 *Assessable Profit:* ₦{data['assessable_profit']:,.2f}
🏷️ *Company Size:* {data['company_size']}

💰 *Tax Breakdown:*
• CIT Rate: {tax_rate_display}
• CIT Due: ₦{data['cit']:,.2f}
• Education Tax (3%): ₦{data['education_tax']:,.2f}
• IT Levy (1%): ₦{data['it_levy']:,.2f}
• Minimum Tax: ₦{data['minimum_tax']:,.2f}

🧾 *Total Tax Payable:* ₦{data['total_tax']:,.2f}
📊 *Effective Rate:* {data['effective_rate']}% of turnover
"""

def format_vat_summary(data):
    if data["is_inclusive"]:
        return f"""
🧾 *NIGERIA VAT CALCULATOR (7.5%)*

💰 *Amount (VAT Inclusive):* ₦{data['original_amount']:,.2f}

📊 *Breakdown:*
• VAT (7.5%): ₦{data['vat']:,.2f}
• Amount (Exclusive): ₦{data['exclusive_amount']:,.2f}
• Total (Inclusive): ₦{data['total_with_vat']:,.2f}
"""
    else:
        return f"""
🧾 *NIGERIA VAT CALCULATOR (7.5%)*

💰 *Amount (VAT Exclusive):* ₦{data['original_amount']:,.2f}

📊 *Breakdown:*
• VAT (7.5%): ₦{data['vat']:,.2f}
• Total (Inclusive): ₦{data['total_with_vat']:,.2f}
"""

def format_history_summary(history):
    if not history:
        return "📋 *No calculation history found.*\n\nStart calculating taxes to see your history here!"
    
    message = "📋 *YOUR CALCULATION HISTORY*\n\n"
    for idx, calc in enumerate(history[:10], 1):
        date = datetime.fromisoformat(calc["created_at"]).strftime("%b %d, %H:%M")
        calc_type = calc["calculation_type"].upper()
        input_data = json.loads(calc["input_data"])
        
        if calc_type == "PAYE":
            message += f"{idx}. *{calc_type}* - ₦{input_data.get('salary', 0):,.0f} → ₦{json.loads(calc['result_data']).get('monthly_tax', 0):,.0f} tax\n"
        elif calc_type == "CIT":
            message += f"{idx}. *{calc_type}* - ₦{input_data.get('turnover', 0):,.0f} → ₦{json.loads(calc['result_data']).get('total_tax', 0):,.0f} tax\n"
        elif calc_type == "VAT":
            message += f"{idx}. *{calc_type}* - ₦{input_data.get('amount', 0):,.0f} → VAT ₦{json.loads(calc['result_data']).get('vat', 0):,.0f}\n"
    
    message += "\n📊 Use `/stats` to see your usage statistics."
    return message

def format_stats_summary(stats, user_id):
    if not stats:
        return "📊 *No statistics available.*\n\nMake some calculations to see your stats!"
    
    joined = datetime.fromisoformat(stats["joined_at"]).strftime("%b %d, %Y") if stats["joined_at"] else "Unknown"
    
    return f"""
📊 *YOUR TAX BOT STATISTICS*

👤 *User ID:* `{user_id[:16]}...`
📅 *Joined:* {joined}
🔄 *Last Active:* {stats.get('last_active', 'N/A')[:10] if stats.get('last_active') else 'N/A'}

📈 *Total Calculations:* {stats['total_calculations']}

*Breakdown:*
• PAYE Calculations: {stats['paye_count']}
• CIT Calculations: {stats['cit_count']}
• VAT Calculations: {stats['vat_count']}

💡 You're helping make Nigerian taxes easier to understand!
"""

# ============ MESSAGE SENDING FUNCTIONS ============
def send_telegram_message(chat_id, text):
    if not TELEGRAM_TOKEN:
        return False
    
    url = f"{TELEGRAM_API_URL}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        return True
    except Exception as e:
        logging.error(f"Telegram send failed: {e}")
        return False

def send_whatsapp_message(to_number, text):
    if not WHATSAPP_ACCESS_TOKEN or not PHONE_NUMBER_ID:
        return False
    
    url = f"{WHATSAPP_API_URL}/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}", "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to_number, "type": "text", "text": {"body": text}}
    
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        response.raise_for_status()
        return True
    except Exception as e:
        logging.error(f"WhatsApp send failed: {e}")
        return False

# ============ CRON JOB FUNCTIONS ============
def get_upcoming_deadlines(days_ahead=7):
    today = datetime.now()
    upcoming = []
    
    for deadline in TAX_DEADLINES:
        if 'day' in deadline and 'month' not in deadline:
            next_date = datetime(today.year, today.month, deadline['day'])
            if next_date < today:
                if today.month == 12:
                    next_date = datetime(today.year + 1, 1, deadline['day'])
                else:
                    next_date = datetime(today.year, today.month + 1, deadline['day'])
            
            days_until = (next_date - today).days
            if 0 <= days_until <= days_ahead:
                upcoming.append({"name": deadline['name'], "date": next_date.strftime("%B %d, %Y"), "days": days_until, "description": deadline['description']})
        
        elif 'month' in deadline and 'day' in deadline:
            next_date = datetime(today.year, deadline['month'], deadline['day'])
            if next_date < today:
                next_date = datetime(today.year + 1, deadline['month'], deadline['day'])
            
            days_until = (next_date - today).days
            if 0 <= days_until <= days_ahead:
                upcoming.append({"name": deadline['name'], "date": next_date.strftime("%B %d, %Y"), "days": days_until, "description": deadline['description']})
    
    return sorted(upcoming, key=lambda x: x['days'])

def format_deadline_message(deadlines):
    if not deadlines:
        return "No tax deadlines in the next 7 days. ✅"
    
    message = "📅 *NIGERIA TAX DEADLINE REMINDERS*\n\n"
    for dl in deadlines:
        if dl['days'] == 0:
            message += f"⚠️ *TODAY:* {dl['name']}\n"
        elif dl['days'] == 1:
            message += f"🔔 *Tomorrow:* {dl['name']}\n"
        else:
            message += f"📌 *{dl['name']}* - {dl['days']} days left\n"
        message += f"   _{dl['description']}_\n\n"
    
    message += "\n💡 Send /help for tax calculation assistance."
    return message

def get_daily_tax_tip():
    tips = [
        "💡 *Tax Tip:* Consolidated Relief Allowance (CRA) = ₦200,000 OR 1% of gross + 20% of gross - whichever is higher.",
        "💡 *Tax Tip:* Pension contributions (8%) are tax-deductible. Ensure your employer remits correctly.",
        "💡 *Tax Tip:* NHF contributions of 2.5% are mandatory but tax-deductible.",
        "💡 *Tax Tip:* VAT in Nigeria is 7.5% - always add to taxable goods and services.",
        "💡 *Tax Tip:* Late filing penalties: ₦50,000 for individuals, ₦500,000 for companies.",
        "💡 *Tax Tip:* Small companies (turnover < ₦25M) are exempt from CIT.",
        "💡 *Tax Tip:* Education Tax is 3% of assessable profit for all companies.",
        "💡 *Tax Tip:* Input VAT can be deducted from Output VAT - only pay the difference!",
        "💡 *Tax Tip:* VAT returns are due by the 21st of every month.",
    ]
    return random.choice(tips)

# ============ WHATSAPP WEBHOOK VERIFICATION ============
def verify_whatsapp_webhook(mode, token, challenge):
    if mode and token:
        if mode == "subscribe" and token == WHATSAPP_VERIFY_TOKEN:
            return challenge
    return None

def process_whatsapp_message(message_data):
    try:
        entry = message_data.get('entry', [{}])[0]
        changes = entry.get('changes', [{}])[0]
        value = changes.get('value', {})
        messages = value.get('messages', [])
        
        if not messages:
            return None, None
        
        message = messages[0]
        from_number = message.get('from')
        msg_type = message.get('type')
        
        if msg_type == 'text':
            text = message.get('text', {}).get('body', '').strip()
            return from_number, text
        
        return from_number, None
    except Exception as e:
        logging.error(f"WhatsApp process error: {e}")
        return None, None

# ============ FLASK ENDPOINTS ============

@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        "status": "healthy",
        "telegram": bool(TELEGRAM_TOKEN),
        "whatsapp": bool(WHATSAPP_ACCESS_TOKEN),
        "supabase": bool(supabase),
        "timestamp": datetime.now().isoformat()
    }), 200

@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    try:
        update = request.get_json()
        
        if not update or 'message' not in update:
            return jsonify({"status": "ok"}), 200
        
        message = update['message']
        chat_id = str(message['chat']['id'])
        user_name = message.get('from', {}).get('first_name', 'User')
        text = message.get('text', '').strip()
        
        logging.info(f"Telegram from {chat_id}: {text}")
        
        # Get or create user in database
        get_or_create_user("telegram", chat_id, user_name)
        
        # /start command
        if text == '/start':
            welcome = """
🇳🇬 *Nigerian Tax Bot*

Calculate taxes for individuals, companies, and VAT.

*Commands:*
• Send any number - Calculate PAYE tax
• /paye 500000 - Calculate PAYE
• /cit 50000000 - Company Income Tax
• /vat 100000 - Calculate VAT
• /vatin 100000 - VAT inclusive
• /vatliability 500000 750000 - VAT liability
• /history - Your calculation history
• /stats - Your usage statistics
• /deadlines - Tax deadlines
• /tip - Daily tax tip
• /help - Full menu

Your calculations are saved to track your history!
"""
            send_telegram_message(chat_id, welcome)
            return jsonify({"status": "ok"}), 200
        
        # /help command
        if text == '/help':
            help_text = """
🇳🇬 *Nigerian Tax Bot Commands*

*PAYE Tax (Individual)*
• Send any number - Calculate PAYE
• /paye 500000 - PAYE for ₦500,000

*Company Tax (CIT)*
• /cit 50000000 - CIT for ₦50M turnover

*VAT Calculator*
• /vat 100000 - Add 7.5% VAT
• /vatin 100000 - Extract VAT from total
• /vatliability 500000 750000 - Input vs Output VAT

*Account*
• /history - Your calculation history
• /stats - Your usage statistics

*General*
• /deadlines - Tax deadlines
• /tip - Daily tax tip

💾 All your calculations are saved!
"""
            send_telegram_message(chat_id, help_text)
            return jsonify({"status": "ok"}), 200
        
        # /history command
        if text == '/history':
            history = get_user_history(chat_id)
            send_telegram_message(chat_id, format_history_summary(history))
            return jsonify({"status": "ok"}), 200
        
        # /stats command
        if text == '/stats':
            stats = get_user_stats(chat_id)
            send_telegram_message(chat_id, format_stats_summary(stats, chat_id))
            return jsonify({"status": "ok"}), 200
        
        # /paye command
        if text.startswith('/paye '):
            parts = text.split()
            try:
                salary = float(parts[1].replace(',', ''))
                if salary <= 0:
                    send_telegram_message(chat_id, "Please enter a positive amount.")
                else:
                    data = calculate_nigerian_paye(salary)
                    send_telegram_message(chat_id, format_paye_summary(data))
                    log_calculation(chat_id, "paye", {"salary": salary}, data)
            except ValueError:
                send_telegram_message(chat_id, "Example: `/paye 500000`")
            return jsonify({"status": "ok"}), 200
        
        # /cit command
        if text.startswith('/cit '):
            parts = text.split()
            try:
                turnover = float(parts[1].replace(',', ''))
                profit = float(parts[2].replace(',', '')) if len(parts) > 2 else None
                
                if turnover <= 0:
                    send_telegram_message(chat_id, "Please enter a positive turnover amount.")
                else:
                    data = calculate_company_income_tax(turnover, profit)
                    send_telegram_message(chat_id, format_cit_summary(data))
                    log_calculation(chat_id, "cit", {"turnover": turnover, "profit": profit}, data)
            except ValueError:
                send_telegram_message(chat_id, "Example: `/cit 50000000`")
            return jsonify({"status": "ok"}), 200
        
        # /vat command
        if text.startswith('/vat '):
            parts = text.split()
            try:
                amount = float(parts[1].replace(',', ''))
                if amount <= 0:
                    send_telegram_message(chat_id, "Please enter a positive amount.")
                else:
                    data = calculate_vat(amount, is_inclusive=False)
                    send_telegram_message(chat_id, format_vat_summary(data))
                    log_calculation(chat_id, "vat", {"amount": amount, "type": "exclusive"}, data)
            except ValueError:
                send_telegram_message(chat_id, "Example: `/vat 100000`")
            return jsonify({"status": "ok"}), 200
        
        # /vatin command
        if text.startswith('/vatin '):
            parts = text.split()
            try:
                amount = float(parts[1].replace(',', ''))
                if amount <= 0:
                    send_telegram_message(chat_id, "Please enter a positive amount.")
                else:
                    data = calculate_vat(amount, is_inclusive=True)
                    send_telegram_message(chat_id, format_vat_summary(data))
                    log_calculation(chat_id, "vat", {"amount": amount, "type": "inclusive"}, data)
            except ValueError:
                send_telegram_message(chat_id, "Example: `/vatin 107500`")
            return jsonify({"status": "ok"}), 200
        
        # /vatliability command
        if text.startswith('/vatliability '):
            parts = text.split()
            try:
                input_vat = float(parts[1].replace(',', ''))
                output_vat = float(parts[2].replace(',', '')) if len(parts) > 2 else 0
                data = calculate_vat_liability(input_vat, output_vat)
                send_telegram_message(chat_id, f"""
🏢 *VAT LIABILITY CALCULATION*

📥 *Input VAT (Paid):* ₦{data['input_vat']:,.2f}
📤 *Output VAT (Collected):* ₦{data['output_vat']:,.2f}

📊 *Net {data['status']}:* ₦{abs(data['net_liability']):,.2f}
""")
                log_calculation(chat_id, "vat_liability", {"input_vat": input_vat, "output_vat": output_vat}, data)
            except (ValueError, IndexError):
                send_telegram_message(chat_id, "Example: `/vatliability 500000 750000`")
            return jsonify({"status": "ok"}), 200
        
        # /deadlines command
        if text == '/deadlines':
            deadlines = get_upcoming_deadlines(30)
            send_telegram_message(chat_id, format_deadline_message(deadlines))
            return jsonify({"status": "ok"}), 200
        
        # /tip command
        if text == '/tip':
            send_telegram_message(chat_id, get_daily_tax_tip())
            return jsonify({"status": "ok"}), 200
        
        # Default: parse salary number
        salary_match = re.search(r'[\d,]+', text.replace(',', ''))
        
        if salary_match:
            monthly_salary = float(salary_match.group())
            if monthly_salary <= 0:
                send_telegram_message(chat_id, "Please enter a positive amount.")
            else:
                tax_data = calculate_nigerian_paye(monthly_salary)
                send_telegram_message(chat_id, format_paye_summary(tax_data))
                log_calculation(chat_id, "paye", {"salary": monthly_salary}, tax_data)
        else:
            send_telegram_message(chat_id, "Send a salary amount or use /help for commands.")
        
        return jsonify({"status": "ok"}), 200
        
    except Exception as e:
        logging.error(f"Telegram error: {e}")
        return jsonify({"status": "error"}), 500

@app.route('/api/whatsapp/webhook', methods=['GET', 'POST'])
def whatsapp_webhook():
    if request.method == 'GET':
        mode = request.args.get('hub.mode')
        token = request.args.get('hub.verify_token')
        challenge = request.args.get('hub.challenge')
        
        result = verify_whatsapp_webhook(mode, token, challenge)
        if result:
            return result, 200
        return "Verification failed", 403
    
    elif request.method == 'POST':
        try:
            body = request.get_json()
            from_number, message_text = process_whatsapp_message(body)
            
            if from_number and message_text:
                get_or_create_user("whatsapp", from_number)
                
                salary_match = re.search(r'[\d,]+', message_text.replace(',', ''))
                
                if salary_match:
                    salary = float(salary_match.group())
                    if salary > 0:
                        data = calculate_nigerian_paye(salary)
                        response = format_paye_summary(data)
                        send_whatsapp_message(from_number, response)
                        log_calculation(from_number, "paye", {"salary": salary}, data)
                elif message_text.lower() in ['/start', 'start', 'help']:
                    response = """🇳🇬 Nigerian Tax Bot

Send your monthly salary to calculate PAYE tax.

Commands:
/paye [amount] - Calculate PAYE
/cit [turnover] - Company tax
/vat [amount] - Add VAT
/history - Your calculation history
/deadlines - Tax deadlines
/tip - Tax tips

Your calculations are saved!"""
                    send_whatsapp_message(from_number, response)
            
            return jsonify({"status": "ok"}), 200
        except Exception as e:
            logging.error(f"WhatsApp error: {e}")
            return jsonify({"status": "error"}), 500

# ============ ADMIN BROADCAST ENDPOINT ============
@app.route('/api/admin/broadcast', methods=['POST'])
def admin_broadcast():
    """Admin endpoint to broadcast messages to all users (Protected by secret key)"""
    try:
        data = request.get_json()
        admin_key = data.get('admin_key')
        message = data.get('message')
        platform = data.get('platform')  # 'telegram', 'whatsapp', or 'all'
        
        # Verify admin key from environment
        ADMIN_KEY = os.getenv("ADMIN_KEY")
        if not ADMIN_KEY or admin_key != ADMIN_KEY:
            return jsonify({"error": "Unauthorized"}), 401
        
        if not message:
            return jsonify({"error": "Message required"}), 400
        
        users = []
        if platform == 'all' or platform == 'telegram':
            telegram_users = get_all_active_users('telegram')
            users.extend(telegram_users)
        
        if platform == 'all' or platform == 'whatsapp':
            whatsapp_users = get_all_active_users('whatsapp')
            users.extend(whatsapp_users)
        
        sent = 0
        for user in users:
            if user['platform'] == 'telegram':
                if send_telegram_message(user['user_id'], message):
                    sent += 1
            elif user['platform'] == 'whatsapp':
                if send_whatsapp_message(user['user_id'], message):
                    sent += 1
        
        return jsonify({"status": "success", "sent": sent, "total": len(users)}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ============ CRON JOB ENDPOINTS ============

@app.route('/api/cron/send-deadline-reminders', methods=['POST', 'GET'])
def send_deadline_reminders():
    try:
        deadlines = get_upcoming_deadlines(7)
        message = format_deadline_message(deadlines)
        
        # Send to test users
        if TEST_TELEGRAM_CHAT_ID and TELEGRAM_TOKEN:
            send_telegram_message(TEST_TELEGRAM_CHAT_ID, message)
        
        if TEST_WHATSAPP_NUMBER and WHATSAPP_ACCESS_TOKEN:
            send_whatsapp_message(TEST_WHATSAPP_NUMBER, message)
        
        # Broadcast to all active users
        all_users = get_all_active_users()
        broadcast_message(all_users, message, 'all')
        
        return jsonify({"status": "success", "deadlines_sent": len(deadlines)}), 200
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route('/api/cron/daily-tax-tip', methods=['POST', 'GET'])
def send_daily_tax_tip():
    try:
        tip = get_daily_tax_tip()
        message = f"{tip}\n\nSend your salary to calculate PAYE tax!"
        
        # Send to test users
        if TEST_TELEGRAM_CHAT_ID and TELEGRAM_TOKEN:
            send_telegram_message(TEST_TELEGRAM_CHAT_ID, message)
        
        if TEST_WHATSAPP_NUMBER and WHATSAPP_ACCESS_TOKEN:
            send_whatsapp_message(TEST_WHATSAPP_NUMBER, message)
        
        # Broadcast to all active users
        all_users = get_all_active_users()
        broadcast_message(all_users, message, 'all')
        
        return jsonify({"status": "success", "tip": tip}), 200
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route('/api/cron/check-deadlines', methods=['GET'])
def check_deadlines():
    deadlines = get_upcoming_deadlines(30)
    return jsonify({"deadlines": deadlines}), 200

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8000))
    app.run(host='0.0.0.0', port=port)