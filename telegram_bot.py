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
    logging.warning("⚠️ Supabase not configured")

# ============ TELEGRAM CONFIGURATION ============
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

if TELEGRAM_TOKEN:
    logging.info(f"✅ TELEGRAM_TOKEN loaded")
else:
    logging.error("❌ TELEGRAM_TOKEN NOT FOUND!")

# ============ WHATSAPP CONFIGURATION ============
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "your_verify_token_here")
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
WHATSAPP_API_URL = "https://graph.facebook.com/v18.0"

# ============ CRON JOB TEST USERS ============
TEST_TELEGRAM_CHAT_ID = os.getenv("TEST_TELEGRAM_CHAT_ID")
TEST_WHATSAPP_NUMBER = os.getenv("TEST_WHATSAPP_NUMBER")

# ============ FILING SESSIONS ============
user_filing_sessions = {}

class FilingSession:
    def __init__(self, user_id, tax_type):
        self.user_id = user_id
        self.tax_type = tax_type
        self.step = 1
        self.data = {}
        self.documents = []
        self.started_at = datetime.now()
    
    def get_current_step_question(self):
        steps = {
            "paye": {
                1: "What is your company's TIN?",
                2: "How many employees are you filing for?",
                3: "What is the filing month? (e.g., January 2024)",
                4: "Do you have the PAYE computation file ready? (Yes/No)",
                5: "Have you remitted the PAYE amount? (Yes/No)",
            },
            "cit": {
                1: "What is your company's TIN?",
                2: "What is your company's turnover for the year?",
                3: "What is your assessable profit?",
                4: "Do you have audited financial statements? (Yes/No)",
                5: "Have you filed all quarterly returns? (Yes/No)",
            },
            "vat": {
                1: "What is your company's TIN?",
                2: "What is your monthly output VAT (collected from customers)?",
                3: "What is your monthly input VAT (paid to suppliers)?",
                4: "Do you have all sales invoices? (Yes/No)",
                5: "Do you have all purchase invoices? (Yes/No)",
            },
            "wht": {
                1: "What is your company's TIN?",
                2: "How many payments did you make this month?",
                3: "What was the total amount subject to WHT?",
                4: "Do you have credit notes for all deductions? (Yes/No)",
                5: "Have you issued WHT certificates to vendors? (Yes/No)",
            }
        }
        return steps.get(self.tax_type, steps["paye"]).get(self.step, "Processing your filing...")
    
    def process_answer(self, answer):
        step_fields = {
            "paye": {
                1: "tin",
                2: "employee_count",
                3: "filing_month",
                4: "has_computation",
                5: "has_remitted"
            },
            "cit": {
                1: "tin",
                2: "turnover",
                3: "profit",
                4: "has_audited",
                5: "has_quarterly_filed"
            },
            "vat": {
                1: "tin",
                2: "output_vat",
                3: "input_vat",
                4: "has_sales_invoices",
                5: "has_purchase_invoices"
            },
            "wht": {
                1: "tin",
                2: "payment_count",
                3: "total_amount",
                4: "has_credit_notes",
                5: "has_certificates"
            }
        }
        
        field_name = step_fields.get(self.tax_type, step_fields["paye"]).get(self.step)
        self.data[field_name] = answer
        self.step += 1
        
        return self.is_complete()
    
    def is_complete(self):
        return self.step > 5
    
    def get_final_summary(self):
        if self.tax_type == "paye":
            return f"""
📋 *PAYE FILING CHECKLIST COMPLETE*

✅ TIN: {self.data.get('tin', 'N/A')}
✅ Employees: {self.data.get('employee_count', 'N/A')}
✅ Month: {self.data.get('filing_month', 'N/A')}
✅ Computation: {self.data.get('has_computation', 'N/A')}
✅ Remittance: {self.data.get('has_remitted', 'N/A')}

*Next Steps:*
1. Log into FIRS e-PAYE portal
2. Upload Schedule 6 form
3. Make payment if not already done
4. Keep payment receipt

🔗 https://e-paye.firs.gov.ng
"""
        elif self.tax_type == "cit":
            return f"""
🏢 *CIT FILING CHECKLIST COMPLETE*

✅ TIN: {self.data.get('tin', 'N/A')}
✅ Turnover: ₦{self.data.get('turnover', 'N/A'):,.0f}
✅ Profit: ₦{self.data.get('profit', 'N/A'):,.0f}
✅ Audited Statements: {self.data.get('has_audited', 'N/A')}
✅ Quarterly Filed: {self.data.get('has_quarterly_filed', 'N/A')}

*Next Steps:*
1. Prepare audited financial statements
2. Complete Form A and Form B
3. File via FIRS e-Filing portal
4. Pay assessed tax by March 31

🔗 https://e-filing.firs.gov.ng
"""
        elif self.tax_type == "vat":
            liability = float(self.data.get('output_vat', 0)) - float(self.data.get('input_vat', 0))
            return f"""
🧾 *VAT FILING CHECKLIST COMPLETE*

✅ TIN: {self.data.get('tin', 'N/A')}
✅ Output VAT: ₦{self.data.get('output_vat', 'N/A'):,.0f}
✅ Input VAT: ₦{self.data.get('input_vat', 'N/A'):,.0f}
✅ Net Payable: *₦{max(0, liability):,.0f}*
✅ Sales Invoices: {self.data.get('has_sales_invoices', 'N/A')}
✅ Purchase Invoices: {self.data.get('has_purchase_invoices', 'N/A')}

*Next Steps:*
1. Complete Form 002
2. File via FIRS VAT portal
3. Pay by 21st of next month
4. File monthly returns

🔗 https://vat.firs.gov.ng
"""
        else:
            return f"""
📊 *WHT FILING CHECKLIST COMPLETE*

✅ TIN: {self.data.get('tin', 'N/A')}
✅ Payments: {self.data.get('payment_count', 'N/A')}
✅ Total Amount: ₦{self.data.get('total_amount', 'N/A'):,.0f}
✅ Credit Notes: {self.data.get('has_credit_notes', 'N/A')}
✅ Certificates: {self.data.get('has_certificates', 'N/A')}

*Next Steps:*
1. Complete Form 1
2. File via FIRS e-Filing portal
3. Issue credit notes to vendors
4. File by 21st of next month

🔗 https://e-filing.firs.gov.ng
"""

# ============ DOCUMENT CHECKLIST ============
DOCUMENT_CHECKLISTS = {
    "paye": [
        "Employee payroll register for the month",
        "Individual PAYE computations for each employee",
        "Schedule 6 (PAYE remittance form)",
        "Bank teller/payment confirmation for remittance",
        "Employee biodata (Name, TIN, Basic salary, Allowances)",
        "Previous month's filing reference number"
    ],
    "cit": [
        "Audited financial statements for the year",
        "Form A (Annual returns)",
        "Form B (Tax computation)",
        "Schedule 3 (Capital allowances calculation)",
        "Withholding tax schedule for the year",
        "PAYE remittance summary for the year",
        "Auditor's report and opinion",
        "Company TIN certificate and registration documents",
        "Minutes of Directors meeting approving accounts"
    ],
    "vat": [
        "Sales invoice register for the month",
        "Purchase invoice register for the month",
        "Form 002 (VAT returns)",
        "Input VAT supporting invoices (must be original)",
        "Output VAT supporting invoices",
        "Bank payment confirmation and teller",
        "VAT certificate of registration",
        "Credit notes issued and received"
    ],
    "wht": [
        "Payment schedule for the month",
        "Form 1 (WHT returns)",
        "Credit notes issued to each vendor",
        "WHT certificate for each deduction",
        "Vendor TIN list and verification",
        "Bank payment confirmation",
        "WHT schedule for CIT credit"
    ]
}

FILING_CHECKLISTS = {
    "paye": {
        "title": "📋 PAYE FILING CHECKLIST",
        "steps": [
            "Step 1: Calculate PAYE for each employee",
            "Step 2: Deduct PAYE, Pension (8%), NHF (2.5%)",
            "Step 3: Prepare Schedule 6 form",
            "Step 4: Log into FIRS e-PAYE portal",
            "Step 5: Upload Schedule 6 and pay",
            "Step 6: Download payment receipt",
            "Step 7: Update employee records"
        ]
    },
    "cit": {
        "title": "🏢 CIT FILING CHECKLIST",
        "steps": [
            "Step 1: Prepare audited financial statements",
            "Step 2: Calculate CIT (20% or 30% of profit)",
            "Step 3: Calculate Education Tax (3%)",
            "Step 4: Complete Form A and Form B",
            "Step 5: File via FIRS e-Filing portal",
            "Step 6: Make payment by March 31",
            "Step 7: Keep all documents for 6 years"
        ]
    },
    "vat": {
        "title": "🧾 VAT FILING CHECKLIST",
        "steps": [
            "Step 1: Calculate Output VAT (7.5% of sales)",
            "Step 2: Calculate Input VAT (7.5% of purchases)",
            "Step 3: Net VAT = Output - Input",
            "Step 4: Complete Form 002",
            "Step 5: File by 21st of next month",
            "Step 6: Make payment if net is positive",
            "Step 7: Keep all invoices for verification"
        ]
    },
    "wht": {
        "title": "📊 WHT FILING CHECKLIST",
        "steps": [
            "Step 1: Identify eligible payments (consultancy, rent, etc.)",
            "Step 2: Deduct WHT at applicable rate (10%, 5%, or 3%)",
            "Step 3: Prepare Form 1",
            "Step 4: File by 21st of next month",
            "Step 5: Issue credit notes to vendors",
            "Step 6: Remit deducted amount to FIRS",
            "Step 7: Keep WHT schedule for CIT credit"
        ]
    }
}

def get_filing_checklist(tax_type):
    checklist = FILING_CHECKLISTS.get(tax_type, FILING_CHECKLISTS["paye"])
    steps = "\n".join(checklist["steps"])
    return f"""
{checklist['title']}

{steps}

💡 *Tip:* Use /file{tax_type} to start guided filing
"""

def get_document_checklist(tax_type):
    docs = DOCUMENT_CHECKLISTS.get(tax_type, DOCUMENT_CHECKLISTS["paye"])
    doc_list = "\n".join([f"✅ {doc}" for doc in docs])
    return f"""
📄 *REQUIRED DOCUMENTS FOR {tax_type.upper()} FILING*

{doc_list}

⚠️ *Keep all documents for at least 6 years!*
"""

# ============ TAX CALENDAR DATA ============
TAX_CALENDAR = {
    1: {14: {"name": "PAYE Remittance (Dec)", "type": "paye"}, 21: {"name": "VAT Filing (Dec)", "type": "vat"}},
    2: {14: {"name": "PAYE Remittance (Jan)", "type": "paye"}, 21: {"name": "VAT Filing (Jan)", "type": "vat"}},
    3: {14: {"name": "PAYE Remittance (Feb)", "type": "paye"}, 21: {"name": "VAT Filing (Feb)", "type": "vat"}, 31: {"name": "Annual CIT Filing", "type": "cit"}},
    4: {14: {"name": "PAYE Remittance (Mar)", "type": "paye"}, 21: {"name": "VAT Filing (Mar)", "type": "vat"}, 30: {"name": "Q1 CIT Filing", "type": "cit"}},
    5: {14: {"name": "PAYE Remittance (Apr)", "type": "paye"}, 21: {"name": "VAT Filing (Apr)", "type": "vat"}},
    6: {14: {"name": "PAYE Remittance (May)", "type": "paye"}, 21: {"name": "VAT Filing (May)", "type": "vat"}},
    7: {14: {"name": "PAYE Remittance (Jun)", "type": "paye"}, 21: {"name": "VAT Filing (Jun)", "type": "vat"}, 31: {"name": "Q2 CIT Filing", "type": "cit"}},
    8: {14: {"name": "PAYE Remittance (Jul)", "type": "paye"}, 21: {"name": "VAT Filing (Jul)", "type": "vat"}},
    9: {14: {"name": "PAYE Remittance (Aug)", "type": "paye"}, 21: {"name": "VAT Filing (Aug)", "type": "vat"}},
    10: {14: {"name": "PAYE Remittance (Sep)", "type": "paye"}, 21: {"name": "VAT Filing (Sep)", "type": "vat"}, 31: {"name": "Q3 CIT Filing", "type": "cit"}},
    11: {14: {"name": "PAYE Remittance (Oct)", "type": "paye"}, 21: {"name": "VAT Filing (Oct)", "type": "vat"}},
    12: {14: {"name": "PAYE Remittance (Nov)", "type": "paye"}, 21: {"name": "VAT Filing (Nov)", "type": "vat"}, 31: {"name": "Year-end Planning", "type": "general"}},
}

MONTH_NAMES = {
    1: "January", 2: "February", 3: "March", 4: "April",
    5: "May", 6: "June", 7: "July", 8: "August",
    9: "September", 10: "October", 11: "November", 12: "December"
}

def get_month_calendar(year, month):
    cal = calendar.monthcalendar(year, month)
    month_name = MONTH_NAMES[month]
    deadlines = TAX_CALENDAR.get(month, {})
    
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
    
    result += "└─────┴─────┴─────┴─────┴─────┴─────┴─────┘\n"
    return result

def get_upcoming_deadlines(days_ahead=30):
    today = datetime.now()
    upcoming = []
    
    for month in range(today.month, today.month + 2):
        current_month = ((month - 1) % 12) + 1
        year = today.year + (month - 1) // 12
        
        deadlines = TAX_CALENDAR.get(current_month, {})
        for day, info in deadlines.items():
            deadline_date = datetime(year, current_month, day)
            if deadline_date >= today:
                days = (deadline_date - today).days
                if days <= days_ahead:
                    upcoming.append({
                        "date": deadline_date,
                        "days": days,
                        "name": info["name"],
                        "type": info["type"]
                    })
    
    return sorted(upcoming, key=lambda x: x["days"])[:10]

# ============ WHT RATES ============
WHT_RATES = {
    "consultancy": 10, "rent": 10, "interest": 10, "dividend": 10,
    "construction": 5, "contracts": 5, "transport": 3
}

# ============ CALCULATION FUNCTIONS ============
def calculate_nigerian_paye(monthly_gross):
    annual_gross = monthly_gross * 12
    pension = monthly_gross * 0.08
    nhf = monthly_gross * 0.025
    
    cra_fixed = 200000
    cra_one_percent = annual_gross * 0.01
    cra_base = max(cra_fixed, cra_one_percent)
    cra_percentage = annual_gross * 0.20
    cra_total = cra_base + cra_percentage
    
    total_deductions = (pension * 12) + (nhf * 12) + cra_total
    chargeable = annual_gross - total_deductions
    chargeable = max(0, chargeable)
    
    if chargeable <= 300000:
        tax = chargeable * 0.07
    elif chargeable <= 600000:
        tax = 21000 + (chargeable - 300000) * 0.11
    elif chargeable <= 1100000:
        tax = 54000 + (chargeable - 600000) * 0.15
    elif chargeable <= 1600000:
        tax = 129000 + (chargeable - 1100000) * 0.19
    elif chargeable <= 3200000:
        tax = 224000 + (chargeable - 1600000) * 0.21
    else:
        tax = 560000 + (chargeable - 3200000) * 0.24
    
    if tax < annual_gross * 0.01:
        tax = annual_gross * 0.01
    
    monthly_tax = tax / 12
    effective_rate = (tax / annual_gross) * 100
    
    return {
        "gross": monthly_gross,
        "pension": round(pension, 2),
        "nhf": round(nhf, 2),
        "tax": round(monthly_tax, 2),
        "net": round(monthly_gross - pension - nhf - monthly_tax, 2),
        "rate": round(effective_rate, 2)
    }

def calculate_cit(turnover, profit=None):
    if profit is None:
        profit = turnover * 0.20
    if turnover < 25000000:
        rate = 0
        size = "Small (Exempt)"
    elif turnover <= 100000000:
        rate = 0.20
        size = "Medium"
    else:
        rate = 0.30
        size = "Large"
    
    cit = profit * rate
    education = profit * 0.03
    total = cit + education
    
    return {"turnover": turnover, "profit": profit, "size": size, "total": round(total, 2), "rate": rate}

def calculate_vat(amount, inclusive=False):
    if inclusive:
        vat = amount * 0.075 / 1.075
        exclusive = amount - vat
    else:
        vat = amount * 0.075
        exclusive = amount
    return {"amount": amount, "vat": round(vat, 2), "exclusive": round(exclusive, 2), "total": round(amount + vat, 2) if not inclusive else amount}

def calculate_wht(amount, trans_type):
    rate = WHT_RATES.get(trans_type, 10)
    wht = amount * rate / 100
    return {"amount": amount, "rate": rate, "wht": round(wht, 2), "net": round(amount - wht, 2)}

# ============ FORMATTING ============
def format_paye(data):
    return f"""
🇳🇬 *PAYE SUMMARY*

Gross: ₦{data['gross']:,.0f}
Pension: ₦{data['pension']:,.0f}
NHF: ₦{data['nhf']:,.0f}
Tax: ₦{data['tax']:,.0f}
Net: *₦{data['net']:,.0f}*
Rate: {data['rate']}%
"""

def format_cit(data):
    return f"""
🏢 *CIT SUMMARY*

Turnover: ₦{data['turnover']:,.0f}
Profit: ₦{data['profit']:,.0f}
Size: {data['size']}
Total Tax: *₦{data['total']:,.0f}*
"""

def format_vat(data):
    if 'exclusive' in data and data['exclusive'] != data['amount']:
        return f"""
🧾 *VAT (7.5%)*

Amount (incl): ₦{data['amount']:,.0f}
VAT: ₦{data['vat']:,.0f}
Exclusive: ₦{data['exclusive']:,.0f}
"""
    else:
        return f"""
🧾 *VAT (7.5%)*

Amount (excl): ₦{data['amount']:,.0f}
VAT: ₦{data['vat']:,.0f}
Total: ₦{data['total']:,.0f}
"""

def format_wht(data):
    return f"""
📊 *WITHHOLDING TAX*

Amount: ₦{data['amount']:,.0f}
Rate: {data['rate']}%
WHT: *₦{data['wht']:,.0f}*
Net Payment: ₦{data['net']:,.0f}
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
            new_user = {"platform": platform, "user_id": str(user_id), "name": name, "created_at": datetime.now().isoformat(), "total_calculations": 0, "is_active": True}
            result = supabase.table("users").insert(new_user).execute()
            return result.data[0] if result.data else None
    except Exception as e:
        return None

def log_calculation(user_id, calc_type, input_data, result_data):
    if not supabase:
        return False
    try:
        supabase.table("calculations").insert({"user_id": str(user_id), "calculation_type": calc_type, "input_data": json.dumps(input_data), "result_data": json.dumps(result_data), "created_at": datetime.now().isoformat()}).execute()
        supabase.table("users").update({"total_calculations": supabase.raw("total_calculations + 1"), "last_active": datetime.now().isoformat()}).eq("user_id", str(user_id)).execute()
        return True
    except Exception as e:
        return False

def get_user_history(user_id):
    if not supabase:
        return []
    try:
        response = supabase.table("calculations").select("*").eq("user_id", str(user_id)).order("created_at", desc=True).limit(10).execute()
        return response.data
    except Exception as e:
        return []

# ============ MESSAGE SENDING ============
def send_telegram_message(chat_id, text):
    if not TELEGRAM_TOKEN:
        return False
    try:
        url = f"{TELEGRAM_API_URL}/sendMessage"
        requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}, timeout=10)
        return True
    except Exception as e:
        return False

def send_whatsapp_message(to_number, text):
    if not WHATSAPP_ACCESS_TOKEN or not PHONE_NUMBER_ID:
        return False
    try:
        url = f"{WHATSAPP_API_URL}/{PHONE_NUMBER_ID}/messages"
        headers = {"Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}", "Content-Type": "application/json"}
        payload = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to_number, "type": "text", "text": {"body": text}}
        requests.post(url, json=payload, headers=headers, timeout=10)
        return True
    except Exception as e:
        return False

# ============ WHATSAPP WEBHOOK ============
def verify_whatsapp_webhook(mode, token, challenge):
    if mode and token and mode == "subscribe" and token == WHATSAPP_VERIFY_TOKEN:
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
        return None, None

# ============ FLASK ENDPOINTS ============

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "healthy", "telegram": bool(TELEGRAM_TOKEN), "timestamp": datetime.now().isoformat()})

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
        
        # ============ FILING SESSION HANDLER ============
        if chat_id in user_filing_sessions:
            session = user_filing_sessions[chat_id]
            if not session.is_complete():
                session.process_answer(text)
                if session.is_complete():
                    send_telegram_message(chat_id, session.get_final_summary())
                    del user_filing_sessions[chat_id]
                else:
                    send_telegram_message(chat_id, session.get_current_step_question())
                return jsonify({"status": "ok"}), 200
        
        # ============ FILING COMMANDS ============
        if text == '/filepaye':
            session = FilingSession(chat_id, "paye")
            user_filing_sessions[chat_id] = session
            send_telegram_message(chat_id, f"📋 *PAYE Filing Assistant*\n\n{session.get_current_step_question()}")
            return jsonify({"status": "ok"}), 200
        
        if text == '/filecit':
            session = FilingSession(chat_id, "cit")
            user_filing_sessions[chat_id] = session
            send_telegram_message(chat_id, f"🏢 *CIT Filing Assistant*\n\n{session.get_current_step_question()}")
            return jsonify({"status": "ok"}), 200
        
        if text == '/filevat':
            session = FilingSession(chat_id, "vat")
            user_filing_sessions[chat_id] = session
            send_telegram_message(chat_id, f"🧾 *VAT Filing Assistant*\n\n{session.get_current_step_question()}")
            return jsonify({"status": "ok"}), 200
        
        if text == '/filewht':
            session = FilingSession(chat_id, "wht")
            user_filing_sessions[chat_id] = session
            send_telegram_message(chat_id, f"📊 *WHT Filing Assistant*\n\n{session.get_current_step_question()}")
            return jsonify({"status": "ok"}), 200
        
        # ============ CHECKLIST COMMANDS ============
        if text == '/checklist':
            menu = """
📋 *FILING CHECKLISTS*

/payelist - PAYE filing steps
/citlist - CIT filing steps
/vatlist - VAT filing steps
/whtlist - WHT filing steps

/docs - Required documents
"""
            send_telegram_message(chat_id, menu)
            return jsonify({"status": "ok"}), 200
        
        if text == '/payelist':
            send_telegram_message(chat_id, get_filing_checklist("paye"))
            return jsonify({"status": "ok"}), 200
        if text == '/citlist':
            send_telegram_message(chat_id, get_filing_checklist("cit"))
            return jsonify({"status": "ok"}), 200
        if text == '/vatlist':
            send_telegram_message(chat_id, get_filing_checklist("vat"))
            return jsonify({"status": "ok"}), 200
        if text == '/whtlist':
            send_telegram_message(chat_id, get_filing_checklist("wht"))
            return jsonify({"status": "ok"}), 200
        
        if text == '/docs':
            menu = """
📄 *REQUIRED DOCUMENTS*

/docs paye - PAYE documents
/docs cit - CIT documents
/docs vat - VAT documents
/docs wht - WHT documents
"""
            send_telegram_message(chat_id, menu)
            return jsonify({"status": "ok"}), 200
        
        if text.startswith('/docs '):
            parts = text.split()
            tax_type = parts[1].lower() if len(parts) > 1 else "paye"
            send_telegram_message(chat_id, get_document_checklist(tax_type))
            return jsonify({"status": "ok"}), 200
        
        # ============ CALENDAR COMMANDS ============
        if text == '/calendar':
            today = datetime.now()
            send_telegram_message(chat_id, get_month_calendar(today.year, today.month))
            return jsonify({"status": "ok"}), 200
        
        if text.startswith('/calendar '):
            parts = text.split()
            try:
                month = int(parts[1])
                if 1 <= month <= 12:
                    year = datetime.now().year
                    if month < datetime.now().month:
                        year += 1
                    send_telegram_message(chat_id, get_month_calendar(year, month))
                else:
                    send_telegram_message(chat_id, "Month must be 1-12")
            except:
                send_telegram_message(chat_id, "Example: /calendar 6")
            return jsonify({"status": "ok"}), 200
        
        if text == '/deadlines':
            upcoming = get_upcoming_deadlines(30)
            if not upcoming:
                send_telegram_message(chat_id, "✅ No deadlines in next 30 days")
            else:
                msg = "📅 *UPCOMING DEADLINES*\n\n"
                for d in upcoming:
                    if d['days'] == 0:
                        msg += f"⚠️ *TODAY:* {d['name']}\n"
                    elif d['days'] == 1:
                        msg += f"🔔 *TOMORROW:* {d['name']}\n"
                    else:
                        msg += f"📌 *{d['date'].strftime('%b %d')}:* {d['name']} ({d['days']} days)\n"
                send_telegram_message(chat_id, msg)
            return jsonify({"status": "ok"}), 200
        
        # ============ START COMMAND ============
        if text == '/start':
            welcome = """
🇳🇬 *Nigerian Tax Bot Pro*

Complete tax assistant with filing wizard!

*📋 Filing Assistant (New!)*
/filepaye - Guided PAYE filing
/filecit - Guided CIT filing
/filevat - Guided VAT filing
/filewht - Guided WHT filing

*📋 Checklists*
/payelist - PAYE steps
/citlist - CIT steps
/vatlist - VAT steps
/whtlist - WHT steps
/docs - Required documents

*📅 Calendar*
/calendar - View this month
/calendar 6 - View specific month
/deadlines - Upcoming deadlines

*📊 Calculations*
Send salary - PAYE
/cit 50000000 - CIT
/vat 100000 - VAT
/wht 500000 consultancy - WHT

💡 *Try /filepaye to start guided filing!*
"""
            send_telegram_message(chat_id, welcome)
            return jsonify({"status": "ok"}), 200
        
        # ============ HELP ============
        if text == '/help':
            help_text = """
🇳🇬 *Tax Bot Help*

*Filing Assistant*
/filepaye - Guided PAYE filing
/filecit - Guided CIT filing
/filevat - Guided VAT filing
/filewht - Guided WHT filing

*Checklists*
/payelist - PAYE filing steps
/citlist - CIT filing steps
/vatlist - VAT filing steps
/whtlist - WHT filing steps
/docs - Required documents

*Calendar*
/calendar - Monthly calendar
/deadlines - Upcoming deadlines

*Calculations*
Send number - PAYE tax
/cit 50000000 - CIT
/vat 100000 - VAT
/wht 500000 consultancy - WHT
"""
            send_telegram_message(chat_id, help_text)
            return jsonify({"status": "ok"}), 200
        
        # ============ CALCULATIONS ============
        if text.startswith('/cit '):
            parts = text.split()
            try:
                turnover = float(parts[1].replace(',', ''))
                profit = float(parts[2].replace(',', '')) if len(parts) > 2 else None
                data = calculate_cit(turnover, profit)
                send_telegram_message(chat_id, format_cit(data))
                log_calculation(chat_id, "cit", {"turnover": turnover}, data)
            except:
                send_telegram_message(chat_id, "Example: /cit 50000000")
            return jsonify({"status": "ok"}), 200
        
        if text.startswith('/vat '):
            parts = text.split()
            try:
                amount = float(parts[1].replace(',', ''))
                data = calculate_vat(amount, False)
                send_telegram_message(chat_id, format_vat(data))
                log_calculation(chat_id, "vat", {"amount": amount}, data)
            except:
                send_telegram_message(chat_id, "Example: /vat 100000")
            return jsonify({"status": "ok"}), 200
        
        if text.startswith('/vatin '):
            parts = text.split()
            try:
                amount = float(parts[1].replace(',', ''))
                data = calculate_vat(amount, True)
                send_telegram_message(chat_id, format_vat(data))
                log_calculation(chat_id, "vat", {"amount": amount}, data)
            except:
                send_telegram_message(chat_id, "Example: /vatin 107500")
            return jsonify({"status": "ok"}), 200
        
        if text.startswith('/wht '):
            parts = text.split()
            try:
                amount = float(parts[1].replace(',', ''))
                trans_type = parts[2].lower() if len(parts) > 2 else "consultancy"
                data = calculate_wht(amount, trans_type)
                send_telegram_message(chat_id, format_wht(data))
                log_calculation(chat_id, "wht", {"amount": amount, "type": trans_type}, data)
            except:
                send_telegram_message(chat_id, "Example: /wht 500000 consultancy\nTypes: consultancy, rent, construction, transport")
            return jsonify({"status": "ok"}), 200
        
        if text == '/whtrates':
            rates = "📊 *WHT RATES*\n\n10%: Consultancy, Rent, Interest, Dividend\n5%: Construction, Contracts\n3%: Transportation"
            send_telegram_message(chat_id, rates)
            return jsonify({"status": "ok"}), 200
        
        # ============ HISTORY ============
        if text == '/history':
            history = get_user_history(chat_id)
            if not history:
                send_telegram_message(chat_id, "No history yet. Make some calculations!")
            else:
                msg = "📋 *YOUR HISTORY*\n\n"
                for h in history[:5]:
                    date = datetime.fromisoformat(h['created_at']).strftime("%b %d")
                    msg += f"{date}: {h['calculation_type'].upper()}\n"
                send_telegram_message(chat_id, msg)
            return jsonify({"status": "ok"}), 200
        
        # ============ DEFAULT: SALARY ============
        salary_match = re.search(r'[\d,]+', text.replace(',', ''))
        if salary_match:
            salary = float(salary_match.group())
            if salary > 0:
                data = calculate_nigerian_paye(salary)
                send_telegram_message(chat_id, format_paye(data))
                log_calculation(chat_id, "paye", {"salary": salary}, data)
        else:
            send_telegram_message(chat_id, "Send salary or use /help\n\n📋 *Try /filepaye for guided filing*")
        
        return jsonify({"status": "ok"}), 200
        
    except Exception as e:
        logging.error(f"Error: {e}")
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
                        send_whatsapp_message(from_number, format_paye(data))
                elif message_text.lower() in ['/start', 'start', 'help']:
                    send_whatsapp_message(from_number, "🇳🇬 Tax Bot Pro\n\n/filepaye - Guided filing\n/calendar - Tax deadlines\nSend salary - PAYE calculation")
            return jsonify({"status": "ok"}), 200
        except Exception as e:
            return jsonify({"status": "error"}), 500

# ============ CRON JOB ENDPOINTS ============
@app.route('/api/cron/send-deadline-reminders', methods=['POST', 'GET'])
def send_deadline_reminders():
    try:
        upcoming = get_upcoming_deadlines(7)
        if upcoming:
            msg = "📅 *TAX DEADLINE ALERTS*\n\n"
            for d in upcoming[:5]:
                if d['days'] == 0:
                    msg += f"⚠️ *TODAY:* {d['name']}\n"
                elif d['days'] == 1:
                    msg += f"🔔 *TOMORROW:* {d['name']}\n"
                else:
                    msg += f"📌 {d['name']} - {d['days']} days\n"
            if TEST_TELEGRAM_CHAT_ID:
                send_telegram_message(TEST_TELEGRAM_CHAT_ID, msg)
        return jsonify({"status": "success"}), 200
    except Exception as e:
        return jsonify({"status": "error"}), 500

@app.route('/api/cron/daily-tip', methods=['POST', 'GET'])
def send_daily_tip():
    try:
        tips = [
            "💡 Use /filepaye for guided PAYE filing",
            "💡 Keep tax documents for 6 years",
            "💡 VAT returns due by 21st monthly",
            "💡 WHT can be credited against CIT",
            "💡 Use /calendar to track deadlines",
        ]
        tip = random.choice(tips)
        if TEST_TELEGRAM_CHAT_ID:
            send_telegram_message(TEST_TELEGRAM_CHAT_ID, f"{tip}\n\nNeed help? Send /help")
        return jsonify({"status": "success"}), 200
    except Exception as e:
        return jsonify({"status": "error"}), 500

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8000))
    app.run(host='0.0.0.0', port=port)