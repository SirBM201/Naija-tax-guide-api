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

load_dotenv()

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

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
    """
    Calculate Nigerian VAT (7.5%)
    
    Parameters:
    - amount: The transaction amount
    - is_inclusive: If True, amount already includes VAT. If False, VAT is added.
    """
    vat_rate = 0.075
    
    if is_inclusive:
        # Amount includes VAT, calculate VAT portion
        vat = amount * (vat_rate / (1 + vat_rate))
        exclusive_amount = amount - vat
    else:
        # Amount excludes VAT, calculate VAT to add
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
    """
    Calculate VAT payable/refundable for businesses
    Output VAT (collected from customers) - Input VAT (paid to suppliers)
    """
    liability = output_vat - input_vat
    
    status = "Payable to FIRS" if liability > 0 else "Refundable" if liability < 0 else "No liability"
    
    return {
        "input_vat": round(input_vat, 2),
        "output_vat": round(output_vat, 2),
        "net_liability": round(liability, 2),
        "status": status
    }

def format_vat_summary(data):
    """Format VAT calculation for display"""
    if data["is_inclusive"]:
        return f"""
🧾 *NIGERIA VAT CALCULATOR (7.5%)*

💰 *Amount (VAT Inclusive):* ₦{data['original_amount']:,.2f}

📊 *Breakdown:*
• VAT (7.5%): ₦{data['vat']:,.2f}
• Amount (Exclusive): ₦{data['exclusive_amount']:,.2f}
• Total (Inclusive): ₦{data['total_with_vat']:,.2f}

💡 *This amount already includes VAT.*
"""
    else:
        return f"""
🧾 *NIGERIA VAT CALCULATOR (7.5%)*

💰 *Amount (VAT Exclusive):* ₦{data['original_amount']:,.2f}

📊 *Breakdown:*
• VAT (7.5%): ₦{data['vat']:,.2f}
• Total (Inclusive): ₦{data['total_with_vat']:,.2f}

💡 *Add 7.5% VAT to this amount.*
"""

def format_vat_liability_summary(data):
    return f"""
🏢 *VAT LIABILITY CALCULATION*

📥 *Input VAT (Paid):* ₦{data['input_vat']:,.2f}
📤 *Output VAT (Collected):* ₦{data['output_vat']:,.2f}

📊 *Net {data['status']}:* ₦{abs(data['net_liability']):,.2f}

💡 *{'Pay to FIRS by 21st of next month' if data['net_liability'] > 0 else 'Can claim refund or carry forward' if data['net_liability'] < 0 else 'No payment needed'}*
"""

# ============ VAT EXEMPT ITEMS ============
VAT_EXEMPT_ITEMS = [
    "Medical and pharmaceutical products",
    "Basic food items (rice, beans, garri, yam, etc.)",
    "Educational materials and books",
    "Farming equipment and machinery",
    "Medical services",
    "Export services",
    "Plant and machinery for manufacturing"
]

def get_vat_exempt_items():
    """Return list of VAT exempt items in Nigeria"""
    exempt_list = "\n".join([f"• {item}" for item in VAT_EXEMPT_ITEMS])
    return f"""
📋 *VAT EXEMPT ITEMS IN NIGERIA*

The following items are Zero-Rated or Exempt from VAT:

{exempt_list}

⚠️ *Note:* Zero-rated supplies (exports) allow VAT recovery on inputs. Exempt supplies do not.
"""

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
        logging.error("WhatsApp not configured")
        return False
    
    url = f"{WHATSAPP_API_URL}/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}", "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to_number, "type": "text", "text": {"body": text}}
    
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        response.raise_for_status()
        logging.info(f"WhatsApp sent to {to_number}")
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
        "💡 *Tax Tip:* Minimum tax rule applies when chargeable income is low - you still pay 1% of gross.",
        "💡 *Tax Tip:* Basic food items, medical products, and educational materials are VAT exempt.",
        "💡 *Tax Tip:* Small companies (turnover < ₦25M) are exempt from CIT.",
        "💡 *Tax Tip:* Education Tax is 3% of assessable profit for all companies.",
        "💡 *Tax Tip:* Input VAT can be deducted from Output VAT - only pay the difference!",
        "💡 *Tax Tip:* VAT returns are due by the 21st of every month.",
        "💡 *Tax Tip:* Zero-rated exports allow VAT recovery - exempt supplies do not.",
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
        "timestamp": datetime.now().isoformat()
    }), 200

@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    try:
        update = request.get_json()
        
        if not update or 'message' not in update:
            return jsonify({"status": "ok"}), 200
        
        message = update['message']
        chat_id = message['chat']['id']
        text = message.get('text', '').strip()
        
        logging.info(f"Telegram from {chat_id}: {text}")
        
        # /start command
        if text == '/start':
            welcome = """
🇳🇬 *Nigerian Tax Bot*

Calculate taxes for individuals, companies, and VAT.

*Commands:*
• Send any number - Calculate PAYE tax
• /paye 500000 - Calculate PAYE
• /cit 50000000 - Company Income Tax
• /vat 100000 - Calculate VAT (adds 7.5%)
• /vatin 100000 - VAT inclusive (extracts VAT)
• /vatliability 500000 750000 - Input vs Output VAT
• /vatexempt - List VAT exempt items
• /deadlines - Tax deadlines
• /tip - Daily tax tip
• /help - Full menu

Try it now! Send your monthly salary.
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
• /cit 50000000 15000000 - With custom profit

*VAT Calculator*
• /vat 100000 - Add 7.5% VAT
• /vatin 100000 - Extract VAT from total
• /vatliability 500000 750000 - Input vs Output VAT
• /vatexempt - List VAT exempt items

*General*
• /start - Welcome message
• /help - This menu
• /deadlines - Tax deadlines
• /tip - Daily tax tip

*Examples:*
• `500000` - PAYE for ₦500,000
• `/cit 75000000` - CIT for ₦75M
• `/vat 250000` - VAT on ₦250,000
"""
            send_telegram_message(chat_id, help_text)
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
            except ValueError:
                send_telegram_message(chat_id, "Example: `/cit 50000000` or `/cit 50000000 15000000`")
            return jsonify({"status": "ok"}), 200
        
        # /vat command (add VAT)
        if text.startswith('/vat '):
            parts = text.split()
            try:
                amount = float(parts[1].replace(',', ''))
                if amount <= 0:
                    send_telegram_message(chat_id, "Please enter a positive amount.")
                else:
                    data = calculate_vat(amount, is_inclusive=False)
                    send_telegram_message(chat_id, format_vat_summary(data))
            except ValueError:
                send_telegram_message(chat_id, "Example: `/vat 100000`")
            return jsonify({"status": "ok"}), 200
        
        # /vatin command (extract VAT from inclusive amount)
        if text.startswith('/vatin '):
            parts = text.split()
            try:
                amount = float(parts[1].replace(',', ''))
                if amount <= 0:
                    send_telegram_message(chat_id, "Please enter a positive amount.")
                else:
                    data = calculate_vat(amount, is_inclusive=True)
                    send_telegram_message(chat_id, format_vat_summary(data))
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
                send_telegram_message(chat_id, format_vat_liability_summary(data))
            except (ValueError, IndexError):
                send_telegram_message(chat_id, "Example: `/vatliability 500000 750000`\n(Input VAT paid, Output VAT collected)")
            return jsonify({"status": "ok"}), 200
        
        # /vatexempt command
        if text == '/vatexempt':
            send_telegram_message(chat_id, get_vat_exempt_items())
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
                salary_match = re.search(r'[\d,]+', message_text.replace(',', ''))
                
                if salary_match:
                    salary = float(salary_match.group())
                    if salary > 0:
                        data = calculate_nigerian_paye(salary)
                        response = format_paye_summary(data)
                        send_whatsapp_message(from_number, response)
                elif message_text.lower() in ['/start', 'start', 'help']:
                    response = """🇳🇬 Nigerian Tax Bot

Commands:
/paye [amount] - Calculate PAYE
/cit [turnover] - Company tax
/vat [amount] - Add VAT
/vatin [amount] - Extract VAT
/deadlines - Tax deadlines
/tip - Tax tips
/vatexempt - VAT exempt items"""
                    send_whatsapp_message(from_number, response)
            
            return jsonify({"status": "ok"}), 200
        except Exception as e:
            logging.error(f"WhatsApp error: {e}")
            return jsonify({"status": "error"}), 500

# ============ CRON JOB ENDPOINTS ============

@app.route('/api/cron/send-deadline-reminders', methods=['POST', 'GET'])
def send_deadline_reminders():
    try:
        deadlines = get_upcoming_deadlines(7)
        message = format_deadline_message(deadlines)
        
        if TEST_TELEGRAM_CHAT_ID and TELEGRAM_TOKEN:
            send_telegram_message(TEST_TELEGRAM_CHAT_ID, message)
        
        if TEST_WHATSAPP_NUMBER and WHATSAPP_ACCESS_TOKEN:
            send_whatsapp_message(TEST_WHATSAPP_NUMBER, message)
        
        return jsonify({"status": "success", "deadlines_sent": len(deadlines)}), 200
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route('/api/cron/daily-tax-tip', methods=['POST', 'GET'])
def send_daily_tax_tip():
    try:
        tip = get_daily_tax_tip()
        message = f"{tip}\n\nSend your salary to calculate PAYE tax!"
        
        if TEST_TELEGRAM_CHAT_ID and TELEGRAM_TOKEN:
            send_telegram_message(TEST_TELEGRAM_CHAT_ID, message)
        
        if TEST_WHATSAPP_NUMBER and WHATSAPP_ACCESS_TOKEN:
            send_whatsapp_message(TEST_WHATSAPP_NUMBER, message)
        
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