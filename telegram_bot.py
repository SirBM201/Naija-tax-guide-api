import os
import re
import logging
import json
import random
import calendar
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

# ============ TAX CALENDAR DATA ============
TAX_CALENDAR = {
    1: {  # January
        14: {"name": "PAYE Remittance (December)", "type": "paye", "description": "Remit PAYE deducted in December"},
        21: {"name": "VAT Filing (December)", "type": "vat", "description": "File VAT returns for December"},
        31: {"name": "Annual PAYE Returns", "type": "paye", "description": "File annual PAYE summary for previous year"},
    },
    2: {  # February
        14: {"name": "PAYE Remittance (January)", "type": "paye", "description": "Remit PAYE deducted in January"},
        21: {"name": "VAT Filing (January)", "type": "vat", "description": "File VAT returns for January"},
    },
    3: {  # March
        14: {"name": "PAYE Remittance (February)", "type": "paye", "description": "Remit PAYE deducted in February"},
        21: {"name": "VAT Filing (February)", "type": "vat", "description": "File VAT returns for February"},
        31: {"name": "Annual CIT Filing", "type": "cit", "description": "Annual Company Income Tax filing deadline"},
    },
    4: {  # April
        14: {"name": "PAYE Remittance (March)", "type": "paye", "description": "Remit PAYE deducted in March"},
        21: {"name": "VAT Filing (March)", "type": "vat", "description": "File VAT returns for March"},
        30: {"name": "Q1 CIT Filing", "type": "cit", "description": "First quarter CIT filing deadline"},
    },
    5: {  # May
        14: {"name": "PAYE Remittance (April)", "type": "paye", "description": "Remit PAYE deducted in April"},
        21: {"name": "VAT Filing (April)", "type": "vat", "description": "File VAT returns for April"},
    },
    6: {  # June
        14: {"name": "PAYE Remittance (May)", "type": "paye", "description": "Remit PAYE deducted in May"},
        21: {"name": "VAT Filing (May)", "type": "vat", "description": "File VAT returns for May"},
    },
    7: {  # July
        14: {"name": "PAYE Remittance (June)", "type": "paye", "description": "Remit PAYE deducted in June"},
        21: {"name": "VAT Filing (June)", "type": "vat", "description": "File VAT returns for June"},
        31: {"name": "Q2 CIT Filing", "type": "cit", "description": "Second quarter CIT filing deadline"},
    },
    8: {  # August
        14: {"name": "PAYE Remittance (July)", "type": "paye", "description": "Remit PAYE deducted in July"},
        21: {"name": "VAT Filing (July)", "type": "vat", "description": "File VAT returns for July"},
    },
    9: {  # September
        14: {"name": "PAYE Remittance (August)", "type": "paye", "description": "Remit PAYE deducted in August"},
        21: {"name": "VAT Filing (August)", "type": "vat", "description": "File VAT returns for August"},
    },
    10: {  # October
        14: {"name": "PAYE Remittance (September)", "type": "paye", "description": "Remit PAYE deducted in September"},
        21: {"name": "VAT Filing (September)", "type": "vat", "description": "File VAT returns for September"},
        31: {"name": "Q3 CIT Filing", "type": "cit", "description": "Third quarter CIT filing deadline"},
    },
    11: {  # November
        14: {"name": "PAYE Remittance (October)", "type": "paye", "description": "Remit PAYE deducted in October"},
        21: {"name": "VAT Filing (October)", "type": "vat", "description": "File VAT returns for October"},
    },
    12: {  # December
        14: {"name": "PAYE Remittance (November)", "type": "paye", "description": "Remit PAYE deducted in November"},
        21: {"name": "VAT Filing (November)", "type": "vat", "description": "File VAT returns for November"},
        31: {"name": "Year-end Tax Planning", "type": "general", "description": "Review tax position for the year"},
    },
}

MONTH_NAMES = {
    1: "January", 2: "February", 3: "March", 4: "April",
    5: "May", 6: "June", 7: "July", 8: "August",
    9: "September", 10: "October", 11: "November", 12: "December"
}

def get_month_calendar(year, month):
    """Get calendar view for a specific month with tax deadlines marked"""
    cal = calendar.monthcalendar(year, month)
    month_name = MONTH_NAMES[month]
    deadlines = TAX_CALENDAR.get(month, {})
    
    # Build calendar display
    result = f"📅 *{month_name} {year} - Tax Calendar*\n\n"
    result += "┌─────┬─────┬─────┬─────┬─────┬─────┬─────┐\n"
    result += "│ Mon │ Tue │ Wed │ Thu │ Fri │ Sat │ Sun │\n"
    result += "├─────┼─────┼─────┼─────┼─────┼─────┼─────┤\n"
    
    for week in cal:
        for day in week:
            if day == 0:
                result += "│  -  "
            else:
                if day in deadlines:
                    result += f"│ 🔴{day:2d} "
                else:
                    result += f"│  {day:2d}  "
        result += "│\n├─────┼─────┼─────┼─────┼─────┼─────┼─────┤\n"
    
    result += "└─────┴─────┴─────┴─────┴─────┴─────┴─────┘\n\n"
    
    # List deadlines for the month
    if deadlines:
        result += "*📋 Deadlines this month:*\n"
        for day, info in sorted(deadlines.items()):
            result += f"🔴 *{day} {month_name}:* {info['name']}\n"
            result += f"   _{info['description']}_\n\n"
    else:
        result += "✅ *No tax deadlines this month*"
    
    return result

def get_all_upcoming_deadlines(days_ahead=60):
    """Get all upcoming deadlines for the next X days"""
    today = datetime.now()
    upcoming = []
    
    for month in range(today.month, today.month + 3):
        current_month = ((month - 1) % 12) + 1
        year = today.year + (month - 1) // 12
        
        deadlines = TAX_CALENDAR.get(current_month, {})
        
        for day, info in deadlines.items():
            deadline_date = datetime(year, current_month, day)
            if deadline_date >= today:
                days_until = (deadline_date - today).days
                if days_until <= days_ahead:
                    upcoming.append({
                        "date": deadline_date,
                        "days": days_until,
                        "name": info["name"],
                        "type": info["type"],
                        "description": info["description"]
                    })
    
    return sorted(upcoming, key=lambda x: x["days"])

def format_upcoming_deadlines(upcoming):
    """Format upcoming deadlines for display"""
    if not upcoming:
        return "✅ *No tax deadlines in the next 60 days*"
    
    message = "📅 *UPCOMING TAX DEADLINES*\n\n"
    
    for deadline in upcoming[:15]:  # Show next 15 deadlines
        date_str = deadline["date"].strftime("%b %d, %Y")
        
        if deadline["days"] == 0:
            message += f"⚠️ *TODAY:* {deadline['name']}\n"
        elif deadline["days"] == 1:
            message += f"🔔 *TOMORROW:* {deadline['name']}\n"
        else:
            message += f"📌 *{date_str}:* {deadline['name']} ({deadline['days']} days)\n"
        message += f"   _{deadline['description']}_\n\n"
    
    message += "\n💡 Use /calendar [month] to view full month calendar\n"
    message += "Example: /calendar 6 for June, /calendar 12 for December"
    
    return message

def get_tax_type_summary(tax_type):
    """Get summary deadlines for a specific tax type"""
    summary = ""
    
    if tax_type == "paye":
        summary = """
📊 *PAYE DEADLINES SUMMARY*

*Monthly Remittance:*
• Due by 14th of each month
• Remit PAYE deducted from previous month
• File Schedule 6

*Annual Returns:*
• Due by January 31st
• File annual PAYE summary
• Submit for all employees

*Penalties:*
• Late remittance: ₦50,000 + interest
• Late annual returns: ₦50,000 + ₦5,000/day
"""
    elif tax_type == "vat":
        summary = """
🧾 *VAT DEADLINES SUMMARY*

*Monthly Filing:*
• Due by 21st of each month
• File Form 002
• Pay VAT liability

*Annual Returns:*
• Due by January 31st
• File annual VAT summary
• Reconciliation of monthly filings

*Penalties:*
• Late filing: ₦50,000/month
• Late payment: 21% interest + 10% penalty
"""
    elif tax_type == "cit":
        summary = """
🏢 *CIT DEADLINES SUMMARY*

*Quarterly Filings:*
• Q1: April 30
• Q2: July 31
• Q3: October 31

*Annual Filing:*
• Due by March 31 (following year)
• File audited accounts
• Form A and Form B

*Penalties:*
• Late filing: ₦500,000 + 10% of tax
• Underpayment: 21% interest per annum
"""
    elif tax_type == "wht":
        summary = """
📊 *WHT DEADLINES SUMMARY*

*Monthly Filing:*
• Due by 21st of each month
• File Form 1
• Issue credit notes

*Penalties:*
• Late filing: ₦50,000/month
• Late remittance: Interest at CBN rate
"""
    
    return summary

def export_calendar_to_ical(year):
    """Generate iCal format calendar (placeholder - would be full implementation)"""
    ical_content = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Naija Tax Bot//Tax Calendar//EN
CALSCALE:GREGORIAN
METHOD:PUBLISH
"""
    
    for month in range(1, 13):
        deadlines = TAX_CALENDAR.get(month, {})
        for day, info in deadlines.items():
            dtstart = f"{year}{month:02d}{day:02d}"
            ical_content += f"""
BEGIN:VEVENT
UID:{dtstart}@naijataxbot
DTSTAMP:{datetime.now().strftime('%Y%m%dT%H%M%S')}
DTSTART;VALUE=DATE:{dtstart}
SUMMARY:{info['name']}
DESCRIPTION:{info['description']}
END:VEVENT
"""
    
    ical_content += "END:VCALENDAR"
    return ical_content

# ============ WITHHOLDING TAX (WHT) RATES ============
WHT_RATES = {
    "dividend": {"rate": 10, "description": "Dividend payments to shareholders"},
    "interest": {"rate": 10, "description": "Interest payments on loans, bonds, treasury bills"},
    "rent": {"rate": 10, "description": "Rent on land, buildings, and structures"},
    "royalty": {"rate": 10, "description": "Royalty payments for intellectual property"},
    "directors_fees": {"rate": 10, "description": "Directors fees and sitting allowances"},
    "consultancy": {"rate": 10, "description": "Professional, management, and technical services"},
    "construction": {"rate": 5, "description": "Construction and building contracts"},
    "contracts": {"rate": 5, "description": "Supply and service contracts"},
    "commission": {"rate": 10, "description": "Commission and agency fees"},
    "transport": {"rate": 3, "description": "Haulage and transportation services"},
    "management": {"rate": 10, "description": "Management and technical services"},
}

# ============ SALARY COMPARISON SESSIONS ============
user_comparison_sessions = {}

class ComparisonSession:
    def __init__(self, user_id):
        self.user_id = user_id
        self.salaries = []
        self.max_comparisons = 5
        self.started_at = datetime.now()
    
    def add_salary(self, salary):
        if len(self.salaries) < self.max_comparisons:
            tax_data = calculate_nigerian_paye(salary)
            self.salaries.append({
                "salary": salary,
                "monthly_tax": tax_data["monthly_tax"],
                "net_pay": tax_data["net_pay"],
                "effective_rate": tax_data["effective_rate"]
            })
            return True
        return False
    
    def is_full(self):
        return len(self.salaries) >= self.max_comparisons
    
    def get_comparison_message(self):
        if not self.salaries:
            return "No salaries to compare."
        
        message = "📊 *SALARY COMPARISON*\n\n"
        for i, s in enumerate(self.salaries, 1):
            message += f"*{i}.* ₦{s['salary']:,.0f} → ₦{s['net_pay']:,.0f} net\n"
            message += f"   Tax: ₦{s['monthly_tax']:,.0f} ({s['effective_rate']}%)\n\n"
        
        best = max(self.salaries, key=lambda x: x['net_pay'])
        message += f"💡 *Best net:* ₦{best['salary']:,.0f}"
        return message

# ============ TAX QUIZ QUESTIONS ============
TAX_QUIZ_QUESTIONS = [
    {
        "id": 1,
        "question": "What is the current VAT rate in Nigeria?",
        "options": ["5%", "7.5%", "10%", "12.5%"],
        "correct": 1,
        "explanation": "Nigeria's VAT rate is 7.5% as per the Finance Act 2020."
    },
    {
        "id": 2,
        "question": "By which date must PAYE be remitted monthly?",
        "options": ["7th", "14th", "21st", "30th"],
        "correct": 1,
        "explanation": "PAYE remittance is due by the 14th of the following month."
    },
    {
        "id": 3,
        "question": "What is the penalty for late CIT filing?",
        "options": ["₦100,000", "₦250,000", "₦500,000", "₦1,000,000"],
        "correct": 2,
        "explanation": "Late CIT filing penalty is ₦500,000 + 10% of tax due."
    },
    {
        "id": 4,
        "question": "When must VAT returns be filed?",
        "options": ["7th", "14th", "21st", "30th"],
        "correct": 2,
        "explanation": "VAT returns are due by the 21st of the following month."
    },
    {
        "id": 5,
        "question": "What is the Q2 CIT filing deadline?",
        "options": ["April 30", "May 31", "June 30", "July 31"],
        "correct": 3,
        "explanation": "Q2 CIT filing is due by July 31 each year."
    },
]

# ============ QUIZ SESSION MANAGEMENT ============
user_quiz_sessions = {}

class QuizSession:
    def __init__(self, user_id):
        self.user_id = user_id
        self.questions = random.sample(TAX_QUIZ_QUESTIONS, min(5, len(TAX_QUIZ_QUESTIONS)))
        self.current_index = 0
        self.score = 0
        self.started_at = datetime.now()
    
    def get_current_question(self):
        if self.current_index < len(self.questions):
            return self.questions[self.current_index]
        return None
    
    def submit_answer(self, answer_index):
        current = self.get_current_question()
        if not current:
            return None
        
        is_correct = (answer_index == current["correct"])
        if is_correct:
            self.score += 1
        
        self.current_index += 1
        return is_correct
    
    def is_complete(self):
        return self.current_index >= len(self.questions)
    
    def get_result_message(self):
        percentage = (self.score / len(self.questions)) * 100
        
        if percentage >= 80:
            rating = "🌟 Excellent!"
        elif percentage >= 60:
            rating = "👍 Good!"
        else:
            rating = "📚 Keep learning!"
        
        return f"📊 *QUIZ RESULTS*\n\nScore: {self.score}/{len(self.questions)}\n{rating}"

# ============ TAX CALCULATION FUNCTIONS ============
def calculate_nigerian_paye(monthly_gross):
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
    
    if annual_tax < (annual_gross * 0.01):
        annual_tax = annual_gross * 0.01
    
    monthly_tax = annual_tax / 12
    effective_rate = (annual_tax / annual_gross) * 100
    
    return {
        "monthly_gross": monthly_gross,
        "pension": round(pension, 2),
        "nhf": round(nhf, 2),
        "monthly_tax": round(monthly_tax, 2),
        "effective_rate": round(effective_rate, 2),
        "net_pay": round(monthly_gross - pension - nhf - monthly_tax, 2)
    }

def calculate_company_income_tax(annual_turnover, assessable_profit=None):
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
    education_tax = assessable_profit * 0.03
    total_tax = cit + education_tax
    
    return {
        "annual_turnover": round(annual_turnover, 2),
        "company_size": company_size,
        "total_tax": round(total_tax, 2)
    }

def calculate_vat(amount, is_inclusive=False):
    vat_rate = 0.075
    
    if is_inclusive:
        vat = amount * (vat_rate / (1 + vat_rate))
    else:
        vat = amount * vat_rate
    
    return {
        "amount": round(amount, 2),
        "vat": round(vat, 2),
        "total": round(amount + (0 if is_inclusive else vat), 2)
    }

def calculate_withholding_tax(amount, transaction_type):
    rate_info = WHT_RATES.get(transaction_type.lower(), WHT_RATES["consultancy"])
    rate = rate_info["rate"]
    wht_amount = (amount * rate) / 100
    
    return {
        "amount": round(amount, 2),
        "rate": rate,
        "wht_amount": round(wht_amount, 2),
        "net_payment": round(amount - wht_amount, 2)
    }

# ============ FORMATTING FUNCTIONS ============
def format_paye_summary(data):
    return f"""
🇳🇬 *PAYE SUMMARY*

📊 Gross: ₦{data['monthly_gross']:,.0f}
📋 Pension: ₦{data['pension']:,.0f}
📋 NHF: ₦{data['nhf']:,.0f}
🧾 Tax: ₦{data['monthly_tax']:,.0f}
💵 Net: *₦{data['net_pay']:,.0f}*
📊 Rate: {data['effective_rate']}%
"""

def format_cit_summary(data):
    return f"""
🏢 *CIT SUMMARY*

📊 Turnover: ₦{data['annual_turnover']:,.0f}
🏷️ Size: {data['company_size']}
🧾 Tax: *₦{data['total_tax']:,.0f}*
"""

def format_vat_summary(data):
    return f"""
🧾 *VAT (7.5%)*

💰 Amount: ₦{data['amount']:,.0f}
📊 VAT: ₦{data['vat']:,.0f}
📊 Total: ₦{data['total']:,.0f}
"""

def format_wht_summary(data):
    return f"""
📊 *WITHHOLDING TAX*

💰 Amount: ₦{data['amount']:,.0f}
📊 Rate: {data['rate']}%
🧾 WHT: *₦{data['wht_amount']:,.0f}*
💵 Net: ₦{data['net_payment']:,.0f}
"""

# ============ DATABASE FUNCTIONS ============
def get_or_create_user(platform, user_id, name=None):
    if not supabase:
        return None
    
    try:
        response = supabase.table("users").select("*").eq("platform", platform).eq("user_id", str(user_id)).execute()
        
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
            result = supabase.table("users").insert(new_user).execute()
            return result.data[0] if result.data else None
    except Exception as e:
        logging.error(f"Database error: {e}")
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
        supabase.table("calculations").insert(record).execute()
        
        supabase.table("users").update({
            "total_calculations": supabase.raw("total_calculations + 1"),
            "last_active": datetime.now().isoformat()
        }).eq("user_id", str(user_id)).execute()
        
        return True
    except Exception as e:
        logging.error(f"Log error: {e}")
        return False

def get_user_history(user_id, limit=10):
    if not supabase:
        return None
    
    try:
        response = supabase.table("calculations").select("*").eq("user_id", str(user_id)).order("created_at", desc=True).limit(limit).execute()
        return response.data
    except Exception as e:
        return None

def get_all_active_users(platform=None):
    if not supabase:
        return []
    
    try:
        query = supabase.table("users").select("user_id, platform").eq("is_active", True)
        if platform:
            query = query.eq("platform", platform)
        response = query.execute()
        return response.data
    except Exception as e:
        return []

def broadcast_message(users, message, platform_type):
    sent = 0
    for user in users:
        if platform_type == "telegram":
            if send_telegram_message(user["user_id"], message):
                sent += 1
    return sent

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
        logging.error(f"Send failed: {e}")
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
def get_daily_tax_tip():
    tips = [
        "📅 *Calendar Tip:* PAYE due by 14th monthly. Add to your calendar with /calendar",
        "📅 *Calendar Tip:* VAT due by 21st monthly. Use /calendar to track deadlines",
        "📅 *Calendar Tip:* Q1 CIT due April 30. Check /calendar for all quarterly dates",
        "📅 *Calendar Tip:* Annual CIT due March 31. Never miss a deadline with /deadlines",
        "📅 *Calendar Tip:* Export tax calendar to Google Calendar with /exportcalendar",
    ]
    return random.choice(tips)

# ============ WHATSAPP WEBHOOK ============
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
        
        get_or_create_user("telegram", chat_id, user_name)
        
        # ============ SALARY COMPARISON HANDLER ============
        if chat_id in user_comparison_sessions:
            session = user_comparison_sessions[chat_id]
            if not session.is_full():
                salary_match = re.search(r'[\d,]+', text.replace(',', ''))
                if salary_match:
                    salary = float(salary_match.group())
                    if salary > 0:
                        session.add_salary(salary)
                        if session.is_full():
                            send_telegram_message(chat_id, session.get_comparison_message())
                            log_calculation(chat_id, "compare", {"salaries": session.salaries}, {})
                            del user_comparison_sessions[chat_id]
                        else:
                            remaining = session.max_comparisons - len(session.salaries)
                            send_telegram_message(chat_id, f"✅ Added. Add {remaining} more or send /done")
                    else:
                        send_telegram_message(chat_id, "Enter positive amount.")
                elif text.lower() == '/done':
                    if len(session.salaries) >= 2:
                        send_telegram_message(chat_id, session.get_comparison_message())
                        log_calculation(chat_id, "compare", {"salaries": session.salaries}, {})
                    else:
                        send_telegram_message(chat_id, "Need at least 2 salaries.")
                    del user_comparison_sessions[chat_id]
                return jsonify({"status": "ok"}), 200
        
        # ============ QUIZ HANDLER ============
        if chat_id in user_quiz_sessions:
            session = user_quiz_sessions[chat_id]
            if not session.is_complete():
                if text in ['1', '2', '3', '4']:
                    answer_idx = int(text) - 1
                    is_correct = session.submit_answer(answer_idx)
                    
                    if is_correct:
                        response = "✅ Correct!\n"
                    else:
                        response = "❌ Incorrect!\n"
                    
                    if session.is_complete():
                        response += f"\n{session.get_result_message()}"
                        del user_quiz_sessions[chat_id]
                    else:
                        next_q = session.get_current_question()
                        opts = "\n".join([f"{i+1}. {opt}" for i, opt in enumerate(next_q['options'])])
                        response += f"\n\nNext:\n{next_q['question']}\n\n{opts}\n\nAnswer (1-4):"
                    
                    send_telegram_message(chat_id, response)
                    return jsonify({"status": "ok"}), 200
                else:
                    del user_quiz_sessions[chat_id]
                    send_telegram_message(chat_id, "Quiz cancelled. Send /quiz to start over.")
                    return jsonify({"status": "ok"}), 200
        
        # ============ CALENDAR COMMANDS ============
        if text == '/calendar':
            today = datetime.now()
            calendar_view = get_month_calendar(today.year, today.month)
            calendar_view += f"\n\n📌 *Commands:*\n/calendar [number] - View specific month\n/calendar 6 - View June\n/calendar 12 - View December"
            send_telegram_message(chat_id, calendar_view)
            return jsonify({"status": "ok"}), 200
        
        if text.startswith('/calendar '):
            parts = text.split()
            try:
                month_num = int(parts[1])
                if 1 <= month_num <= 12:
                    today = datetime.now()
                    year = today.year
                    if month_num < today.month:
                        year += 1
                    calendar_view = get_month_calendar(year, month_num)
                    send_telegram_message(chat_id, calendar_view)
                else:
                    send_telegram_message(chat_id, "Please enter month number 1-12")
            except ValueError:
                send_telegram_message(chat_id, "Example: /calendar 6 for June")
            return jsonify({"status": "ok"}), 200
        
        if text == '/deadlines':
            upcoming = get_all_upcoming_deadlines(60)
            send_telegram_message(chat_id, format_upcoming_deadlines(upcoming))
            return jsonify({"status": "ok"}), 200
        
        if text == '/exportcalendar':
            today = datetime.now()
            ical_data = export_calendar_to_ical(today.year)
            send_telegram_message(chat_id, f"📅 *Calendar Export*\n\nGenerate iCal file for {today.year}.\n\nNote: Use this URL to subscribe:\n`https://yourbot.com/api/calendar/ical/{today.year}`")
            return jsonify({"status": "ok"}), 200
        
        if text.startswith('/payesummary'):
            send_telegram_message(chat_id, get_tax_type_summary("paye"))
            return jsonify({"status": "ok"}), 200
        
        if text.startswith('/vatsummary'):
            send_telegram_message(chat_id, get_tax_type_summary("vat"))
            return jsonify({"status": "ok"}), 200
        
        if text.startswith('/citsummary'):
            send_telegram_message(chat_id, get_tax_type_summary("cit"))
            return jsonify({"status": "ok"}), 200
        
        if text.startswith('/whtsummary'):
            send_telegram_message(chat_id, get_tax_type_summary("wht"))
            return jsonify({"status": "ok"}), 200
        
        # ============ START COMMAND ============
        if text == '/start':
            welcome = """
🇳🇬 *Nigerian Tax Bot*

Complete tax assistant with calendar!

*📅 Calendar Features:* 🆕
• /calendar - View this month's tax calendar
• /calendar 6 - View specific month
• /deadlines - Upcoming deadlines (60 days)
• /exportcalendar - Export to iCal
• /payesummary - PAYE deadline summary
• /vatsummary - VAT deadline summary
• /citsummary - CIT deadline summary

*📊 Calculate:*
• Send salary - PAYE
• /paye 500000 - PAYE
• /cit 50000000 - CIT
• /vat 100000 - VAT
• /wht 500000 consultancy - WHT

*📚 Learn:*
• /quiz - Tax quiz
• /compare - Compare salaries

💡 *Start with /calendar to see tax deadlines!*
"""
            send_telegram_message(chat_id, welcome)
            return jsonify({"status": "ok"}), 200
        
        # ============ HELP COMMAND ============
        if text == '/help':
            help_text = """
🇳🇬 *Tax Bot Help*

*📅 Calendar*
/calendar - View monthly calendar
/calendar 6 - View June calendar
/deadlines - Upcoming deadlines
/exportcalendar - Export to iCal
/payesummary - PAYE deadlines
/vatsummary - VAT deadlines
/citsummary - CIT deadlines

*📊 Calculations*
Send number - PAYE tax
/paye 500000 - PAYE
/cit 50000000 - CIT
/vat 100000 - Add VAT
/wht 500000 consultancy - WHT

*📚 Learning*
/quiz - Take quiz
/compare - Compare salaries

💡 *Try /calendar to see tax deadlines visually!*
"""
            send_telegram_message(chat_id, help_text)
            return jsonify({"status": "ok"}), 200
        
        # ============ COMPARE COMMAND ============
        if text == '/compare':
            if chat_id in user_comparison_sessions:
                del user_comparison_sessions[chat_id]
            
            session = ComparisonSession(chat_id)
            user_comparison_sessions[chat_id] = session
            send_telegram_message(chat_id, "📊 *Salary Comparison*\n\nSend first salary amount (e.g., 500000):")
            return jsonify({"status": "ok"}), 200
        
        # ============ QUIZ COMMAND ============
        if text == '/quiz':
            if chat_id in user_quiz_sessions:
                del user_quiz_sessions[chat_id]
            
            session = QuizSession(chat_id)
            user_quiz_sessions[chat_id] = session
            first_q = session.get_current_question()
            opts = "\n".join([f"{i+1}. {opt}" for i, opt in enumerate(first_q['options'])])
            send_telegram_message(chat_id, f"📚 *TAX QUIZ*\n\n{first_q['question']}\n\n{opts}\n\nSend answer (1-4):")
            return jsonify({"status": "ok"}), 200
        
        # ============ WHT COMMAND ============
        if text == '/whtrates':
            rates = "📊 *WHT RATES*\n\n10%: Consultancy, Rent, Interest, Dividend\n5%: Construction, Contracts\n3%: Transportation"
            send_telegram_message(chat_id, rates)
            return jsonify({"status": "ok"}), 200
        
        if text.startswith('/wht '):
            parts = text.split()
            try:
                amount = float(parts[1].replace(',', ''))
                trans_type = parts[2].lower() if len(parts) > 2 else "consultancy"
                data = calculate_withholding_tax(amount, trans_type)
                send_telegram_message(chat_id, format_wht_summary(data))
                log_calculation(chat_id, "wht", {"amount": amount, "type": trans_type}, data)
            except (ValueError, IndexError):
                send_telegram_message(chat_id, "Example: /wht 500000 consultancy")
            return jsonify({"status": "ok"}), 200
        
        # ============ CALCULATION COMMANDS ============
        if text.startswith('/paye '):
            parts = text.split()
            try:
                salary = float(parts[1].replace(',', ''))
                data = calculate_nigerian_paye(salary)
                send_telegram_message(chat_id, format_paye_summary(data))
                log_calculation(chat_id, "paye", {"salary": salary}, data)
            except ValueError:
                send_telegram_message(chat_id, "Example: /paye 500000")
            return jsonify({"status": "ok"}), 200
        
        if text.startswith('/cit '):
            parts = text.split()
            try:
                turnover = float(parts[1].replace(',', ''))
                data = calculate_company_income_tax(turnover)
                send_telegram_message(chat_id, format_cit_summary(data))
                log_calculation(chat_id, "cit", {"turnover": turnover}, data)
            except ValueError:
                send_telegram_message(chat_id, "Example: /cit 50000000")
            return jsonify({"status": "ok"}), 200
        
        if text.startswith('/vat '):
            parts = text.split()
            try:
                amount = float(parts[1].replace(',', ''))
                data = calculate_vat(amount, is_inclusive=False)
                send_telegram_message(chat_id, format_vat_summary(data))
                log_calculation(chat_id, "vat", {"amount": amount}, data)
            except ValueError:
                send_telegram_message(chat_id, "Example: /vat 100000")
            return jsonify({"status": "ok"}), 200
        
        if text.startswith('/vatin '):
            parts = text.split()
            try:
                amount = float(parts[1].replace(',', ''))
                data = calculate_vat(amount, is_inclusive=True)
                send_telegram_message(chat_id, format_vat_summary(data))
                log_calculation(chat_id, "vat", {"amount": amount}, data)
            except ValueError:
                send_telegram_message(chat_id, "Example: /vatin 107500")
            return jsonify({"status": "ok"}), 200
        
        # ============ DEFAULT: SALARY NUMBER ============
        salary_match = re.search(r'[\d,]+', text.replace(',', ''))
        
        if salary_match:
            monthly_salary = float(salary_match.group())
            if monthly_salary > 0:
                tax_data = calculate_nigerian_paye(monthly_salary)
                send_telegram_message(chat_id, format_paye_summary(tax_data))
                log_calculation(chat_id, "paye", {"salary": monthly_salary}, tax_data)
        else:
            send_telegram_message(chat_id, "Send salary or use /help\n\n📅 *Try /calendar to see tax deadlines*")
        
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
                        send_whatsapp_message(from_number, format_paye_summary(data))
                        log_calculation(from_number, "paye", {"salary": salary}, data)
                elif message_text.lower() in ['/start', 'start', 'help']:
                    response = "🇳🇬 Tax Bot\n\n/calendar - View deadlines\n/paye [amount] - Calculate\n/quiz - Test knowledge"
                    send_whatsapp_message(from_number, response)
            
            return jsonify({"status": "ok"}), 200
        except Exception as e:
            logging.error(f"WhatsApp error: {e}")
            return jsonify({"status": "error"}), 500

# ============ CRON JOB ENDPOINTS ============

@app.route('/api/cron/send-deadline-reminders', methods=['POST', 'GET'])
def send_deadline_reminders():
    try:
        upcoming = get_all_upcoming_deadlines(7)
        message = format_upcoming_deadlines(upcoming)
        
        if TEST_TELEGRAM_CHAT_ID:
            send_telegram_message(TEST_TELEGRAM_CHAT_ID, message)
        
        all_users = get_all_active_users("telegram")
        broadcast_message(all_users, message, "telegram")
        
        return jsonify({"status": "success", "deadlines": len(upcoming)}), 200
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route('/api/cron/daily-tax-tip', methods=['POST', 'GET'])
def send_daily_tax_tip():
    try:
        tip = get_daily_tax_tip()
        message = f"{tip}\n\n📅 Use /calendar to never miss tax deadlines!"
        
        if TEST_TELEGRAM_CHAT_ID:
            send_telegram_message(TEST_TELEGRAM_CHAT_ID, message)
        
        return jsonify({"status": "success"}), 200
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route('/api/calendar/ical/<int:year>', methods=['GET'])
def get_ical_calendar(year):
    """Serve iCal calendar file for external calendar apps"""
    ical_content = export_calendar_to_ical(year)
    response = jsonify({"ical_url": f"/api/calendar/ical/{year}/download"})
    return response

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8000))
    app.run(host='0.0.0.0', port=port)