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
TELEGRAM_ENABLED = bool(TELEGRAM_TOKEN)

# ============ WHATSAPP CONFIGURATION ============
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "your_verify_token_here")
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
WHATSAPP_API_URL = "https://graph.facebook.com/v18.0"
WHATSAPP_ENABLED = bool(WHATSAPP_ACCESS_TOKEN and PHONE_NUMBER_ID)

# ============ CRON JOB TEST USERS ============
TEST_TELEGRAM_CHAT_ID = os.getenv("TEST_TELEGRAM_CHAT_ID")
TEST_WHATSAPP_NUMBER = os.getenv("TEST_WHATSAPP_NUMBER")

# ============ USER SESSIONS (for interactive features) ============
user_comparison_sessions = {}
user_quiz_sessions = {}
user_filing_sessions = {}

# ============ LANGUAGE SUPPORT ============
LANGUAGES = {"en": "English", "pidgin": "Pidgin", "yoruba": "Yorùbá", "hausa": "Hausa", "igbo": "Igbo"}
user_language = {}

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

# ============ QUIZ QUESTIONS ============
QUIZ_QUESTIONS = [
    {"q": "What is the current VAT rate in Nigeria?", "opt": ["5%", "7.5%", "10%", "12.5%"], "correct": 1, "exp": "VAT rate is 7.5%"},
    {"q": "By which date must PAYE be remitted?", "opt": ["7th", "14th", "21st", "30th"], "correct": 1, "exp": "PAYE due by 14th monthly"},
    {"q": "What is the CIT rate for large companies?", "opt": ["20%", "25%", "30%", "35%"], "correct": 2, "exp": "Large companies pay 30% CIT"},
    {"q": "When must VAT returns be filed?", "opt": ["7th", "14th", "21st", "30th"], "correct": 2, "exp": "VAT due by 21st monthly"},
    {"q": "What is the WHT rate for consultancy?", "opt": ["5%", "7.5%", "10%", "12.5%"], "correct": 2, "exp": "Consultancy WHT is 10%"},
    {"q": "What is the penalty for late CIT filing?", "opt": ["₦100k", "₦250k", "₦500k", "₦1M"], "correct": 2, "exp": "Late CIT penalty: ₦500k + 10%"},
]

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
    rate = (annual_tax / annual_gross) * 100
    
    return {"gross": monthly_gross, "pension": round(pension), "nhf": round(nhf), "tax": round(monthly_tax), "net": round(monthly_gross - pension - nhf - monthly_tax), "rate": round(rate, 1)}

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

# ============ COMPARISON SESSION ============
class ComparisonSession:
    def __init__(self):
        self.salaries = []
    def add(self, salary):
        self.salaries.append(calculate_paye(salary))
        return len(self.salaries)
    def is_full(self):
        return len(self.salaries) >= 5
    def get_result(self):
        msg = "*SALARY COMPARISON*\n\n"
        for i, s in enumerate(self.salaries, 1):
            msg += f"{i}. ₦{s['gross']:,.0f} → ₦{s['net']:,.0f} net (Tax: ₦{s['tax']:,.0f})\n"
        best = max(self.salaries, key=lambda x: x['net'])
        msg += f"\n*Best net:* ₦{best['gross']:,.0f} → ₦{best['net']:,.0f}"
        return msg

# ============ QUIZ SESSION ============
class QuizSession:
    def __init__(self):
        self.questions = random.sample(QUIZ_QUESTIONS, min(5, len(QUIZ_QUESTIONS)))
        self.index = 0
        self.score = 0
    def current(self):
        if self.index < len(self.questions):
            return self.questions[self.index]
        return None
    def answer(self, choice):
        q = self.current()
        if not q:
            return None
        correct = (choice == q['correct'])
        if correct:
            self.score += 1
        result = {"correct": correct, "explanation": q['exp'], "correct_answer": q['opt'][q['correct']]}
        self.index += 1
        return result
    def is_done(self):
        return self.index >= len(self.questions)
    def get_score(self):
        return f"*QUIZ COMPLETE!*\n\nScore: {self.score}/{len(self.questions)}\nPercentage: {(self.score/len(self.questions))*100:.0f}%"

# ============ FILING SESSION ============
class FilingSession:
    def __init__(self, tax_type):
        self.tax_type = tax_type
        self.step = 1
        self.data = {}
    def get_question(self):
        questions = {
            "paye": ["Send your company TIN:", "Number of employees:", "Filing month (e.g., January 2024):", "PAYE computation ready? (yes/no):", "Payment made? (yes/no):"],
            "cit": ["Send your company TIN:", "Annual turnover (₦):", "Assessable profit (₦):", "Audited statements ready? (yes/no):", "Quarterly returns filed? (yes/no):"],
            "vat": ["Send your company TIN:", "Output VAT collected (₦):", "Input VAT paid (₦):", "Sales invoices ready? (yes/no):", "Purchase invoices ready? (yes/no):"],
            "wht": ["Send your company TIN:", "Number of payments made:", "Total amount (₦):", "Credit notes issued? (yes/no):", "WHT certificates ready? (yes/no):"]
        }
        return questions.get(self.tax_type, questions["paye"])[self.step - 1]
    def process(self, answer):
        fields = {"paye": ["tin", "employees", "month", "computation", "payment"], "cit": ["tin", "turnover", "profit", "audited", "quarterly"], "vat": ["tin", "output", "input", "invoices", "purchases"], "wht": ["tin", "payments", "amount", "credit_notes", "certificates"]}
        self.data[fields.get(self.tax_type, [])[self.step - 1]] = answer
        self.step += 1
        return self.step > 5
    def get_summary(self):
        return f"*FILING CHECKLIST - {self.tax_type.upper()}*\n\nData collected: {len(self.data)} items\n✓ Ready for filing!\n\nUse FIRS e-Filing portal to submit."

# ============ HELPER FUNCTIONS ============
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

def format_deadlines(upcoming):
    if not upcoming:
        return "✅ No tax deadlines in the next 30 days"
    msg = "*TAX DEADLINES*\n\n"
    for d in upcoming:
        if d['days'] == 0:
            msg += f"⚠️ *TODAY:* {d['name']}\n"
        elif d['days'] == 1:
            msg += f"🔔 *TOMORROW:* {d['name']}\n"
        else:
            msg += f"📌 {d['name']} - {d['days']} days\n"
    return msg

def get_help_text():
    return """*TAX BOT HELP*

*Calculations:*
• Send number - PAYE tax
• /paye 500000 - PAYE
• /cit 50000000 - CIT
• /vat 100000 - Add VAT
• /vatin 107500 - Extract VAT
• /wht 500000 consultancy - WHT

*Interactive:*
• /compare - Compare salaries
• /quiz - Tax quiz

*Calendar:*
• /calendar - Tax calendar
• /deadlines - Due dates

*Filing:*
• /filepaye - PAYE filing
• /filecit - CIT filing
• /filevat - VAT filing
• /filewht - WHT filing

*Language:*
• /language - Change language"""

def get_calendar_view():
    today = datetime.now()
    cal = calendar.monthcalendar(today.year, today.month)
    month = today.strftime("%B")
    msg = f"*{month} {today.year} - Tax Calendar*\n\n"
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
    return msg

# ============ MESSAGE SENDING (Universal) ============
def send_message(platform, recipient, text):
    """Unified message sender for both Telegram and WhatsApp"""
    if platform == "telegram" and TELEGRAM_ENABLED:
        try:
            url = f"{TELEGRAM_API_URL}/sendMessage"
            requests.post(url, json={"chat_id": recipient, "text": text, "parse_mode": "Markdown"}, timeout=10)
            return True
        except:
            return False
    elif platform == "whatsapp" and WHATSAPP_ENABLED:
        try:
            url = f"{WHATSAPP_API_URL}/{PHONE_NUMBER_ID}/messages"
            headers = {"Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}", "Content-Type": "application/json"}
            payload = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": recipient, "type": "text", "text": {"body": text}}
            requests.post(url, json=payload, headers=headers, timeout=10)
            return True
        except:
            return False
    return False

def process_command(platform, user_id, text, user_name="User"):
    """Unified command processor for both Telegram and WhatsApp"""
    
    # Get user language (default English for WhatsApp until language feature is used)
    lang = user_language.get(f"{platform}_{user_id}", "en")
    
    # ===== LANGUAGE =====
    if text == '/language':
        msg = "🌍 *Select language:*\n1 English\n2 Pidgin\n3 Yoruba\n4 Hausa\n5 Igbo\n\nSend number:"
        send_message(platform, user_id, msg)
        return True
    
    # Handle language selection
    if text in ['1', '2', '3', '4', '5']:
        lang_map = {"1": "en", "2": "pidgin", "3": "yoruba", "4": "hausa", "5": "igbo"}
        user_language[f"{platform}_{user_id}"] = lang_map[text]
        send_message(platform, user_id, f"✅ Language changed to {LANGUAGES[lang_map[text]]}!")
        return True
    
    # ===== COMPARISON SESSION =====
    session_key = f"{platform}_{user_id}_compare"
    if session_key in user_comparison_sessions:
        session = user_comparison_sessions[session_key]
        salary_match = re.search(r'[\d,]+', text.replace(',', ''))
        if salary_match:
            salary = float(salary_match.group())
            if salary > 0:
                count = session.add(salary)
                if session.is_full():
                    send_message(platform, user_id, session.get_result())
                    del user_comparison_sessions[session_key]
                else:
                    send_message(platform, user_id, f"✅ Added ₦{salary:,.0f}\nSend {5-count} more or 'done' to finish:")
            else:
                send_message(platform, user_id, "Send positive amount")
        elif text.lower() == 'done':
            if len(session.salaries) >= 2:
                send_message(platform, user_id, session.get_result())
            else:
                send_message(platform, user_id, "Need at least 2 salaries to compare")
            del user_comparison_sessions[session_key]
        return True
    
    # ===== QUIZ SESSION =====
    quiz_key = f"{platform}_{user_id}_quiz"
    if quiz_key in user_quiz_sessions:
        session = user_quiz_sessions[quiz_key]
        if text in ['1', '2', '3', '4']:
            result = session.answer(int(text) - 1)
            if result:
                if result['correct']:
                    msg = f"✅ *Correct!* {result['explanation']}\n\nScore: {session.score}/{session.index}"
                else:
                    msg = f"❌ *Incorrect!* Answer: {result['correct_answer']}\n{result['explanation']}\n\nScore: {session.score}/{session.index}"
                
                if session.is_done():
                    msg += f"\n\n{session.get_score()}\n\nSend /quiz for new questions!"
                    del user_quiz_sessions[quiz_key]
                else:
                    q = session.current()
                    opts = "\n".join([f"{i+1}. {opt}" for i, opt in enumerate(q['opt'])])
                    msg += f"\n\n*Next:* {q['q']}\n\n{opts}\n\nSend number (1-4):"
                send_message(platform, user_id, msg)
        else:
            send_message(platform, user_id, "Send number (1-4) for your answer, or /quiz to start over")
        return True
    
    # ===== FILING SESSION =====
    filing_key = f"{platform}_{user_id}_filing"
    if filing_key in user_filing_sessions:
        session = user_filing_sessions[filing_key]
        is_done = session.process(text)
        if is_done:
            send_message(platform, user_id, session.get_summary())
            del user_filing_sessions[filing_key]
        else:
            send_message(platform, user_id, session.get_question())
        return True
    
    # ===== COMMANDS =====
    if text == '/start' or text == 'start':
        msg = """🇳🇬 *NIGERIA TAX BOT*

Complete tax assistant with calculations, calendar, quiz, and filing guides!

*Commands:*
/paye [amount] - PAYE tax
/cit [turnover] - Company tax
/vat [amount] - VAT calculation
/wht [amount] [type] - Withholding tax
/compare - Compare salaries
/quiz - Tax quiz
/calendar - Tax calendar
/deadlines - Due dates
/filepaye - PAYE filing guide
/filecit - CIT filing guide
/filevat - VAT filing guide
/filewht - WHT filing guide
/language - Change language
/help - All commands

Send your salary to calculate PAYE now!"""
        send_message(platform, user_id, msg)
        return True
    
    if text == '/help' or text == 'help':
        send_message(platform, user_id, get_help_text())
        return True
    
    # ===== CALCULATION COMMANDS =====
    if text.startswith('/paye '):
        try:
            salary = float(text.split()[1].replace(',', ''))
            if salary > 0:
                d = calculate_paye(salary)
                msg = f"*PAYE SUMMARY*\n\nGross: ₦{d['gross']:,.0f}\nPension: ₦{d['pension']:,.0f}\nNHF: ₦{d['nhf']:,.0f}\nTax: ₦{d['tax']:,.0f}\nNet: *₦{d['net']:,.0f}*\nRate: {d['rate']}%"
                send_message(platform, user_id, msg)
            else:
                send_message(platform, user_id, "Send positive amount")
        except:
            send_message(platform, user_id, "Example: /paye 500000")
        return True
    
    if text.startswith('/cit '):
        try:
            turnover = float(text.split()[1].replace(',', ''))
            d = calculate_cit(turnover)
            msg = f"*CIT SUMMARY*\n\nTurnover: ₦{d['turnover']:,.0f}\nProfit: ₦{d['profit']:,.0f}\nSize: {d['size']}\nCIT Rate: {d['rate']}%\nTotal Tax: *₦{d['total']:,.0f}*"
            send_message(platform, user_id, msg)
        except:
            send_message(platform, user_id, "Example: /cit 50000000")
        return True
    
    if text.startswith('/vat '):
        try:
            amount = float(text.split()[1].replace(',', ''))
            d = calculate_vat(amount, False)
            msg = f"*VAT (7.5%)*\n\nAmount (excl): ₦{d['amount']:,.0f}\nVAT: ₦{d['vat']:,.0f}\nTotal: ₦{d['total']:,.0f}"
            send_message(platform, user_id, msg)
        except:
            send_message(platform, user_id, "Example: /vat 100000")
        return True
    
    if text.startswith('/vatin '):
        try:
            amount = float(text.split()[1].replace(',', ''))
            d = calculate_vat(amount, True)
            msg = f"*VAT (7.5%)*\n\nAmount (incl): ₦{d['amount']:,.0f}\nVAT: ₦{d['vat']:,.0f}\nExclusive: ₦{d['exclusive']:,.0f}"
            send_message(platform, user_id, msg)
        except:
            send_message(platform, user_id, "Example: /vatin 107500")
        return True
    
    if text.startswith('/wht '):
        parts = text.split()
        try:
            amount = float(parts[1].replace(',', ''))
            ttype = parts[2].lower() if len(parts) > 2 else "consultancy"
            d = calculate_wht(amount, ttype)
            msg = f"*WITHHOLDING TAX*\n\nAmount: ₦{d['amount']:,.0f}\nRate: {d['rate']}%\nWHT: *₦{d['wht']:,.0f}*\nNet Payment: ₦{d['net']:,.0f}"
            send_message(platform, user_id, msg)
        except:
            send_message(platform, user_id, "Example: /wht 500000 consultancy\nTypes: consultancy, rent, interest, construction, transport")
        return True
    
    if text == '/whtrates':
        msg = "*WHT RATES*\n\n10%: Consultancy, Rent, Interest, Dividend\n5%: Construction, Contracts\n3%: Transportation"
        send_message(platform, user_id, msg)
        return True
    
    # ===== INTERACTIVE COMMANDS =====
    if text == '/compare':
        user_comparison_sessions[f"{platform}_{user_id}_compare"] = ComparisonSession()
        send_message(platform, user_id, "*Salary Comparison*\n\nSend up to 5 salary amounts.\n\nSend first salary (e.g., 500000):")
        return True
    
    if text == '/quiz':
        user_quiz_sessions[f"{platform}_{user_id}_quiz"] = QuizSession()
        q = user_quiz_sessions[f"{platform}_{user_id}_quiz"].current()
        opts = "\n".join([f"{i+1}. {opt}" for i, opt in enumerate(q['opt'])])
        send_message(platform, user_id, f"*TAX QUIZ*\n\n{q['q']}\n\n{opts}\n\nSend number (1-4):")
        return True
    
    # ===== CALENDAR COMMANDS =====
    if text == '/calendar':
        send_message(platform, user_id, get_calendar_view())
        return True
    
    if text == '/deadlines':
        send_message(platform, user_id, format_deadlines(get_upcoming_deadlines(30)))
        return True
    
    # ===== FILING COMMANDS =====
    if text == '/filepaye':
        user_filing_sessions[f"{platform}_{user_id}_filing"] = FilingSession("paye")
        send_message(platform, user_id, f"*PAYE FILING ASSISTANT*\n\n{user_filing_sessions[f'{platform}_{user_id}_filing'].get_question()}")
        return True
    
    if text == '/filecit':
        user_filing_sessions[f"{platform}_{user_id}_filing"] = FilingSession("cit")
        send_message(platform, user_id, f"*CIT FILING ASSISTANT*\n\n{user_filing_sessions[f'{platform}_{user_id}_filing'].get_question()}")
        return True
    
    if text == '/filevat':
        user_filing_sessions[f"{platform}_{user_id}_filing"] = FilingSession("vat")
        send_message(platform, user_id, f"*VAT FILING ASSISTANT*\n\n{user_filing_sessions[f'{platform}_{user_id}_filing'].get_question()}")
        return True
    
    if text == '/filewht':
        user_filing_sessions[f"{platform}_{user_id}_filing"] = FilingSession("wht")
        send_message(platform, user_id, f"*WHT FILING ASSISTANT*\n\n{user_filing_sessions[f'{platform}_{user_id}_filing'].get_question()}")
        return True
    
    # ===== DEFAULT: PAYE CALCULATION =====
    salary_match = re.search(r'[\d,]+', text.replace(',', ''))
    if salary_match:
        salary = float(salary_match.group())
        if salary > 0:
            d = calculate_paye(salary)
            msg = f"*PAYE SUMMARY*\n\nGross: ₦{d['gross']:,.0f}\nPension: ₦{d['pension']:,.0f}\nNHF: ₦{d['nhf']:,.0f}\nTax: ₦{d['tax']:,.0f}\nNet: *₦{d['net']:,.0f}*\nRate: {d['rate']}%"
            send_message(platform, user_id, msg)
        else:
            send_message(platform, user_id, "Send positive amount")
    else:
        send_message(platform, user_id, "Send salary amount or use /help for commands")
    
    return True

# ============ FLASK ENDPOINTS ============

@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        "status": "healthy",
        "telegram": TELEGRAM_ENABLED,
        "whatsapp": WHATSAPP_ENABLED,
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
        process_command("telegram", chat_id, text, user_name)
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        logging.error(f"Telegram error: {e}")
        return jsonify({"status": "error"}), 500

@app.route('/api/whatsapp/webhook', methods=['GET', 'POST'])
def whatsapp_webhook():
    # Verification
    if request.method == 'GET':
        mode = request.args.get('hub.mode')
        token = request.args.get('hub.verify_token')
        challenge = request.args.get('hub.challenge')
        if mode and token and mode == "subscribe" and token == WHATSAPP_VERIFY_TOKEN:
            return challenge, 200
        return "Verification failed", 403
    
    # Handle messages
    try:
        body = request.get_json()
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
                process_command("whatsapp", from_number, text)
        
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        logging.error(f"WhatsApp error: {e}")
        return jsonify({"status": "error"}), 500

# ============ CRON JOB ENDPOINTS ============

@app.route('/api/cron/send-deadline-reminders', methods=['POST', 'GET'])
def send_deadline_reminders():
    try:
        upcoming = get_upcoming_deadlines(7)
        msg = format_deadlines(upcoming)
        
        if TEST_TELEGRAM_CHAT_ID and TELEGRAM_ENABLED:
            send_message("telegram", TEST_TELEGRAM_CHAT_ID, msg)
        if TEST_WHATSAPP_NUMBER and WHATSAPP_ENABLED:
            send_message("whatsapp", TEST_WHATSAPP_NUMBER, msg)
        
        return jsonify({"status": "success", "deadlines": len(upcoming)}), 200
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route('/api/cron/daily-tip', methods=['POST', 'GET'])
def daily_tip():
    try:
        tips = [
            "💡 Use /compare to compare multiple salaries!",
            "💡 Use /quiz to test your tax knowledge!",
            "💡 VAT returns are due by 21st of each month!",
            "💡 PAYE must be remitted by 14th monthly!",
            "💡 WHT can be credited against your CIT liability!",
            "💡 Small companies (< ₦25M) are CIT exempt!",
            "💡 Keep tax documents for at least 6 years!"
        ]
        tip = random.choice(tips)
        
        if TEST_TELEGRAM_CHAT_ID and TELEGRAM_ENABLED:
            send_message("telegram", TEST_TELEGRAM_CHAT_ID, f"{tip}\n\nSend /help for more features!")
        if TEST_WHATSAPP_NUMBER and WHATSAPP_ENABLED:
            send_message("whatsapp", TEST_WHATSAPP_NUMBER, f"{tip}\n\nSend /help for more features!")
        
        return jsonify({"status": "success"}), 200
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8000))
    app.run(host='0.0.0.0', port=port)