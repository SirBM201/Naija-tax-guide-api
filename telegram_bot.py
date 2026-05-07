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

# ============ TAX DEADLINES ============
TAX_DEADLINES = [
    {"name": "PAYE Monthly Remittance", "day": 14, "description": "PAYE taxes deducted in previous month must be remitted to FIRS"},
    {"name": "VAT Filing", "day": 21, "description": "Monthly VAT returns filing deadline"},
    {"name": "Company Income Tax (Q1)", "month": 4, "day": 30, "description": "First quarter CIT filing"},
    {"name": "Company Income Tax (Q2)", "month": 7, "day": 31, "description": "Second quarter CIT filing"},
    {"name": "Company Income Tax (Q3)", "month": 10, "day": 31, "description": "Third quarter CIT filing"},
    {"name": "Annual Tax Filing", "month": 3, "day": 31, "description": "Annual individual tax filing deadline"},
]

# ============ TAX QUIZ QUESTIONS ============
TAX_QUIZ_QUESTIONS = [
    {
        "id": 1,
        "question": "What is the current VAT rate in Nigeria?",
        "options": ["5%", "7.5%", "10%", "12.5%"],
        "correct": 1,  # Index 1 = 7.5%
        "explanation": "Nigeria's VAT rate is 7.5% as per the Finance Act 2020."
    },
    {
        "id": 2,
        "question": "What is the Consolidated Relief Allowance (CRA) minimum amount?",
        "options": ["₦100,000", "₦150,000", "₦200,000", "₦250,000"],
        "correct": 2,  # ₦200,000
        "explanation": "CRA minimum is ₦200,000 OR 1% of gross income - whichever is higher."
    },
    {
        "id": 3,
        "question": "What percentage of monthly salary goes to Pension (employee contribution)?",
        "options": ["5%", "6%", "7%", "8%"],
        "correct": 3,  # 8%
        "explanation": "Employees contribute 8% of monthly basic salary to pension."
    },
    {
        "id": 4,
        "question": "What is the CIT rate for large companies (turnover > ₦100M)?",
        "options": ["20%", "25%", "30%", "35%"],
        "correct": 2,  # 30%
        "explanation": "Large companies pay 30% CIT + 3% Education Tax + 1% IT Levy."
    },
    {
        "id": 5,
        "question": "By which date must PAYE be remitted to FIRS monthly?",
        "options": ["7th", "14th", "21st", "30th"],
        "correct": 1,  # 14th
        "explanation": "PAYE remittance is due by the 14th of the following month."
    },
    {
        "id": 6,
        "question": "What is the NHF (National Housing Fund) contribution rate?",
        "options": ["1.5%", "2.0%", "2.5%", "3.0%"],
        "correct": 2,  # 2.5%
        "explanation": "NHF contribution is 2.5% of monthly basic salary."
    },
    {
        "id": 7,
        "question": "Which of these items is VAT EXEMPT in Nigeria?",
        "options": ["Electronics", "Cars", "Medical products", "Furniture"],
        "correct": 2,  # Medical products
        "explanation": "Medical and pharmaceutical products are VAT exempt."
    },
    {
        "id": 8,
        "question": "What is the penalty for late filing of CIT?",
        "options": ["₦100,000", "₦250,000", "₦500,000", "₦1,000,000"],
        "correct": 2,  # ₦500,000
        "explanation": "Late CIT filing penalty is ₦500,000 + 10% of tax due."
    },
    {
        "id": 9,
        "question": "Small companies (turnover < ₦25M) are exempt from which tax?",
        "options": ["PAYE", "VAT", "CIT", "NHF"],
        "correct": 2,  # CIT
        "explanation": "Small companies are exempt from CIT but must file nil returns."
    },
    {
        "id": 10,
        "question": "Education Tax is what percentage of assessable profit?",
        "options": ["2%", "2.5%", "3%", "4%"],
        "correct": 2,  # 3%
        "explanation": "Education Tax is 3% of assessable profit for all companies."
    },
    {
        "id": 11,
        "question": "What is the minimum tax rate for companies?",
        "options": ["0.25%", "0.5%", "0.75%", "1.0%"],
        "correct": 1,  # 0.5%
        "explanation": "Minimum tax is 0.5% of gross turnover or 0.5% of net profit."
    },
    {
        "id": 12,
        "question": "When must VAT returns be filed?",
        "options": ["7th", "14th", "21st", "30th"],
        "correct": 2,  # 21st
        "explanation": "VAT returns are due by the 21st of the following month."
    },
    {
        "id": 13,
        "question": "What does TIN stand for?",
        "options": ["Tax Information Number", "Tax Identification Number", "Total Income Number", "Taxpayer ID Number"],
        "correct": 1,  # Tax Identification Number
        "explanation": "TIN = Tax Identification Number, required for all taxpayers."
    },
    {
        "id": 14,
        "question": "FIRS stands for?",
        "options": ["Federal Inland Revenue Service", "Federal Income Revenue Service", "Federal Internal Revenue Service", "Federal Inland Return Service"],
        "correct": 0,  # Federal Inland Revenue Service
        "explanation": "FIRS = Federal Inland Revenue Service, Nigeria's tax authority."
    },
    {
        "id": 15,
        "question": "What is the penalty for late VAT filing per month?",
        "options": ["₦25,000", "₦50,000", "₦75,000", "₦100,000"],
        "correct": 1,  # ₦50,000
        "explanation": "Late VAT filing penalty is ₦50,000 per month of default."
    }
]

# ============ QUIZ SESSION MANAGEMENT ============
user_quiz_sessions = {}

class QuizSession:
    def __init__(self, user_id):
        self.user_id = user_id
        self.questions = random.sample(TAX_QUIZ_QUESTIONS, min(10, len(TAX_QUIZ_QUESTIONS)))
        self.current_index = 0
        self.score = 0
        self.answers = []
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
        
        self.answers.append({
            "question": current["question"],
            "selected": answer_index,
            "correct": current["correct"],
            "is_correct": is_correct,
            "explanation": current["explanation"]
        })
        
        self.current_index += 1
        return is_correct
    
    def is_complete(self):
        return self.current_index >= len(self.questions)
    
    def get_result_message(self):
        percentage = (self.score / len(self.questions)) * 100
        
        if percentage >= 80:
            rating = "🌟 Excellent! Tax Expert! 🌟"
        elif percentage >= 60:
            rating = "👍 Good! Almost there! 👍"
        elif percentage >= 40:
            rating = "📚 Learning! Study more! 📚"
        else:
            rating = "💪 Keep practicing! Use /learn 💪"
        
        message = f"""
📊 *TAX QUIZ RESULTS*

✅ *Score:* {self.score}/{len(self.questions)}
📈 *Percentage:* {percentage:.1f}%
🏆 *Rating:* {rating}

*Question Breakdown:*
"""
        for i, answer in enumerate(self.answers, 1):
            status = "✅" if answer["is_correct"] else "❌"
            message += f"\n{i}. {status} {answer['question'][:50]}..."
        
        message += f"\n\n📅 *Completed:* {self.started_at.strftime('%Y-%m-%d %H:%M')}"
        
        return message
    
    def get_summary(self):
        return f"Quiz: {self.score}/{len(self.questions)} correct ({self.current_index}/{len(self.questions)} answered)"

# ============ TAX LEARNING MATERIALS ============
def get_tax_learning_material(topic):
    """Get educational content about Nigerian taxes"""
    
    materials = {
        "paye": """
📚 *LEARNING: PAYE (Pay-As-You-Earn)*

*What is PAYE?*
PAYE is tax deducted from employees' salaries by employers and remitted to FIRS.

*How it works:*
1. Employee earns salary
2. Employer calculates tax (after deductions)
3. Tax is deducted at source
4. Remitted to FIRS by 14th of next month

*Tax Rates (Annual):*
• ₦0 - ₦300,000: 7%
• ₦300,001 - ₦600,000: 11%
• ₦600,001 - ₦1,100,000: 15%
• ₦1,100,001 - ₦1,600,000: 19%
• ₦1,600,001 - ₦3,200,000: 21%
• Above ₦3,200,000: 24%

*Allowable Deductions:*
• Pension (8% of basic)
• NHF (2.5% of basic)
• CRA (₦200,000 or 1% + 20% of gross)

💡 *Tip:* The higher your CRA, the less tax you pay!
""",
        "cit": """
📚 *LEARNING: CIT (Company Income Tax)*

*What is CIT?*
Tax paid by companies on their profits.

*Tax Rates by Company Size:*
• Small (< ₦25M): 0% (Exempt)
• Medium (₦25M - ₦100M): 20%
• Large (> ₦100M): 30%

*Additional Taxes:*
• Education Tax: 3% of profit (all companies)
• IT Levy: 1% of profit (large companies)
• Minimum Tax: 0.5% of turnover (applies when CIT is low)

*Filing Deadlines:*
• Q1: April 30
• Q2: July 31
• Q3: October 31
• Annual: March 31

💡 *Tip:* Even small companies must file nil returns!
""",
        "vat": """
📚 *LEARNING: VAT (Value Added Tax)*

*What is VAT?*
Consumption tax on goods and services at 7.5%.

*How VAT Works:*
• Output VAT: Collected from customers
• Input VAT: Paid to suppliers
• Payable: Output - Input

*VAT Returns:*
• File monthly by 21st
• Use Form 002
• Pay difference to FIRS

*Zero-Rated (0%):*
• Exported goods
• Exported services

*Exempt (No VAT):*
• Medical products
• Basic food items
• Educational materials
• Farming equipment

💡 *Tip:* Keep all VAT invoices for input credit!
"""
    }
    
    return materials.get(topic, materials["paye"])

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
            logging.info(f"New user created: {platform}/{user_id}")
            return result.data[0] if result.data else None
    except Exception as e:
        logging.error(f"Database user error: {e}")
        return None

def log_calculation(user_id, calculation_type, input_data, result_data):
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
        
        supabase.table("users").update({
            "total_calculations": supabase.raw("total_calculations + 1"),
            "last_active": datetime.now().isoformat()
        }).eq("user_id", str(user_id)).execute()
        
        return True
    except Exception as e:
        logging.error(f"Log calculation error: {e}")
        return False

def log_quiz_result(user_id, score, total_questions, percentage):
    """Log quiz result to database for tracking progress"""
    if not supabase:
        return False
    
    try:
        record = {
            "user_id": str(user_id),
            "calculation_type": "quiz",
            "input_data": json.dumps({"total": total_questions}),
            "result_data": json.dumps({"score": score, "percentage": percentage}),
            "created_at": datetime.now().isoformat()
        }
        supabase.table("calculations").insert(record).execute()
        return True
    except Exception as e:
        logging.error(f"Log quiz error: {e}")
        return False

def get_user_history(user_id, limit=10):
    if not supabase:
        return None
    
    try:
        response = supabase.table("calculations").select("*").eq("user_id", str(user_id)).order("created_at", desc=True).limit(limit).execute()
        return response.data
    except Exception as e:
        logging.error(f"Get history error: {e}")
        return None

def get_user_stats(user_id):
    if not supabase:
        return None
    
    try:
        user = supabase.table("users").select("*").eq("user_id", str(user_id)).execute()
        calculations = supabase.table("calculations").select("calculation_type").eq("user_id", str(user_id)).execute()
        
        stats = {
            "total_calculations": user.data[0].get("total_calculations", 0) if user.data else 0,
            "joined_at": user.data[0].get("created_at") if user.data else None,
            "last_active": user.data[0].get("last_active") if user.data else None,
            "paye_count": 0,
            "cit_count": 0,
            "vat_count": 0,
            "quiz_count": 0
        }
        
        for calc in calculations.data:
            calc_type = calc.get("calculation_type")
            if calc_type == "paye":
                stats["paye_count"] += 1
            elif calc_type == "cit":
                stats["cit_count"] += 1
            elif calc_type == "vat":
                stats["vat_count"] += 1
            elif calc_type == "quiz":
                stats["quiz_count"] += 1
        
        return stats
    except Exception as e:
        logging.error(f"Get stats error: {e}")
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
        logging.error(f"Get active users error: {e}")
        return []

def broadcast_message(users, message, platform):
    sent_count = 0
    for user in users:
        if platform == "telegram":
            if send_telegram_message(user["user_id"], message):
                sent_count += 1
        elif platform == "whatsapp":
            if send_whatsapp_message(user["user_id"], message):
                sent_count += 1
    return sent_count

# ============ PAYE TAX CALCULATION ============
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
            message += f"{idx}. *{calc_type}* - ₦{input_data.get('salary', 0):,.0f}\n"
        elif calc_type == "CIT":
            message += f"{idx}. *{calc_type}* - ₦{input_data.get('turnover', 0):,.0f}\n"
        elif calc_type == "VAT":
            message += f"{idx}. *{calc_type}* - ₦{input_data.get('amount', 0):,.0f}\n"
        elif calc_type == "QUIZ":
            result = json.loads(calc["result_data"])
            message += f"{idx}. *📚 QUIZ* - Score: {result.get('score', 0)}/{result.get('total', 0)}\n"
    
    return message

def format_stats_summary(stats, user_id):
    if not stats:
        return "📊 *No statistics available.*"
    
    joined = datetime.fromisoformat(stats["joined_at"]).strftime("%b %d, %Y") if stats["joined_at"] else "Unknown"
    
    return f"""
📊 *YOUR TAX BOT STATISTICS*

👤 *User ID:* `{user_id[:16]}...`
📅 *Joined:* {joined}

📈 *Total Activities:* {stats['total_calculations']}

*Breakdown:*
• PAYE Calculations: {stats['paye_count']}
• CIT Calculations: {stats['cit_count']}
• VAT Calculations: {stats['vat_count']}
• 📚 Quiz Attempts: {stats['quiz_count']}

💡 *Try /quiz to test your tax knowledge!*
"""

# ============ TAX FILING GUIDES ============
def get_paye_filing_guide():
    return """
📋 *PAYE FILING GUIDE (Employers)*

*Step 1:* Calculate monthly PAYE for each employee
*Step 2:* Deduct PAYE, Pension (8%), NHF (2.5%)
*Step 3:* File Schedule 6 via FIRS e-PAYE
*Step 4:* Remit by 14th of following month

🔗 *Portal:* https://e-paye.firs.gov.ng
"""

def get_cit_filing_guide():
    return """
🏢 *CIT FILING GUIDE*

*Small (< ₦25M):* Exempt but file nil returns
*Medium (₦25M-₦100M):* 20% CIT + 3% Education Tax
*Large (> ₦100M):* 30% CIT + 3% Education Tax + 1% IT Levy

*Deadlines:* Q1 Apr 30, Q2 Jul 31, Q3 Oct 31, Annual Mar 31

🔗 *Portal:* https://e-filing.firs.gov.ng
"""

def get_vat_filing_guide():
    return """
🧾 *VAT FILING GUIDE*

*Step 1:* Track Output VAT (sales) and Input VAT (purchases)
*Step 2:* Calculate: Output - Input = Amount to pay
*Step 3:* File Form 002 by 21st of following month

🔗 *Portal:* https://vat.firs.gov.ng
"""

def get_filing_checklist(tax_type):
    checklists = {
        "paye": ["Payroll register", "Individual computations", "Schedule 6", "Payment confirmation"],
        "cit": ["Audited accounts", "Form A & B", "Capital allowances schedule", "WHT schedule"],
        "vat": ["Sales register", "Purchase register", "Form 002", "Input VAT invoices"]
    }
    
    items = checklists.get(tax_type, checklists["paye"])
    return f"📋 *{tax_type.upper()} CHECKLIST*\n\n" + "\n".join([f"✓ {item}" for item in items])

def get_filing_deadlines(tax_type):
    deadlines = {
        "paye": "📅 *PAYE:* Due by 14th monthly. Penalty: ₦50,000+",
        "cit": "📅 *CIT:* Q1 Apr 30, Q2 Jul 31, Q3 Oct 31, Annual Mar 31",
        "vat": "📅 *VAT:* Due by 21st monthly. Penalty: ₦50,000/month"
    }
    return deadlines.get(tax_type, deadlines["paye"])

def get_firs_contacts():
    return """
📞 *FIRS CONTACTS*

☎️ Phone: 0700-CALL-FIRS
📧 Email: helpdesk@firs.gov.ng
🌐 Website: https://www.firs.gov.ng
"""

def get_taxpayer_tin_guide():
    return """
🆔 *HOW TO GET TIN*

1. Visit nearest FIRS tax office
2. Complete registration form
3. Provide valid ID and passport photo
4. Get TIN in 2-5 days

💡 TIN is FREE! Beware of scams.
"""

def get_penalties_guide():
    return """
⚠️ *TAX PENALTIES*

• Late PAYE: ₦50,000 + interest
• Late CIT: ₦500,000 + 10% of tax
• Late VAT: ₦50,000/month
• Fraudulent returns: 100% of tax + imprisonment
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
        else:
            message += f"📌 *{dl['name']}* - {dl['days']} days left\n"
        message += f"   _{dl['description']}_\n\n"
    
    return message

def get_daily_tax_tip():
    tips = [
        "💡 CRA = ₦200,000 OR 1% of gross + 20% of gross - whichever is higher.",
        "💡 Pension contributions (8%) are tax-deductible.",
        "💡 VAT in Nigeria is 7.5%.",
        "💡 Late filing penalties: ₦50,000 for individuals, ₦500,000 for companies.",
        "💡 Small companies (turnover < ₦25M) are exempt from CIT.",
        "💡 Education Tax is 3% of assessable profit for all companies.",
        "💡 File PAYE by 14th of each month to avoid penalties.",
        "💡 Medical products and basic food items are VAT exempt.",
        "💡 TIN is FREE - Beware of scams asking for payment.",
        "💡 Keep all tax receipts for at least 6 years.",
        "💡 Try /quiz to test your tax knowledge!",
        "💡 Use /learn to study Nigerian tax basics.",
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
        
        get_or_create_user("telegram", chat_id, user_name)
        
        # ============ QUIZ HANDLER ============
        # Handle quiz answers (single number response during active quiz)
        if chat_id in user_quiz_sessions:
            session = user_quiz_sessions[chat_id]
            if not session.is_complete():
                # Check if answer is 1-4 (quiz option selection)
                if text in ['1', '2', '3', '4']:
                    answer_idx = int(text) - 1
                    is_correct = session.submit_answer(answer_idx)
                    current = session.get_current_question()
                    
                    if is_correct:
                        response = f"✅ *Correct!*\n\n{current['explanation']}\n\n📊 *Progress:* {session.score}/{len(session.questions)} correct"
                    else:
                        correct_option = current['options'][current['correct']]
                        response = f"❌ *Incorrect!*\nCorrect answer: *{correct_option}*\n\n{current['explanation']}\n\n📊 *Progress:* {session.score}/{len(session.questions)} correct"
                    
                    if session.is_complete():
                        log_quiz_result(chat_id, session.score, len(session.questions), (session.score/len(session.questions))*100)
                        response += f"\n\n{session.get_result_message()}\n\n🎯 *Quiz completed!* Try /quiz again for new questions!"
                        del user_quiz_sessions[chat_id]
                    else:
                        next_q = session.get_current_question()
                        options_text = "\n".join([f"{i+1}. {opt}" for i, opt in enumerate(next_q['options'])])
                        response += f"\n\n📋 *Next Question:*\n{next_q['question']}\n\n{options_text}\n\nSend the number of your answer (1-4):"
                    
                    send_telegram_message(chat_id, response)
                    return jsonify({"status": "ok"}), 200
                else:
                    # Cancel quiz if user sends something else
                    del user_quiz_sessions[chat_id]
                    send_telegram_message(chat_id, "❌ Quiz cancelled. Send /quiz to start a new quiz!")
                    return jsonify({"status": "ok"}), 200
        
        # /start command
        if text == '/start':
            welcome = """
🇳🇬 *Nigerian Tax Bot*

Your complete tax assistant for Nigeria!

*Features:*

📊 *Calculate*
• Send salary - PAYE tax
• /paye 500000 - PAYE
• /cit 50000000 - Company tax
• /vat 100000 - VAT

📚 *Learn & Quiz*
• /quiz - Test your tax knowledge
• /learn - Study tax basics

📋 *File Taxes*
• /filepaye - PAYE guide
• /filecit - CIT guide
• /filevat - VAT guide
• /deadlines - Due dates

👤 *Account*
• /history - Your activity
• /stats - Your statistics

💡 *Try /quiz to test your knowledge!*
"""
            send_telegram_message(chat_id, welcome)
            return jsonify({"status": "ok"}), 200
        
        # /help command
        if text == '/help':
            help_text = """
🇳🇬 *Tax Bot Help*

*📊 Calculations*
• Send any number - Calculate PAYE
• /paye 500000 - PAYE
• /cit 50000000 - CIT
• /vat 100000 - Add VAT
• /vatin 107500 - Extract VAT

*📚 Learn & Quiz* 🆕
• /quiz - Take a tax quiz
• /learn - Study materials

*📋 Filing Assistant*
• /filepaye - PAYE guide
• /filecit - CIT guide
• /filevat - VAT guide
• /deadlines - Due dates
• /contacts - FIRS contacts

*👤 Account*
• /history - Your history
• /stats - Your stats
• /tip - Daily tip

💡 *Start with /quiz to test your knowledge!*
"""
            send_telegram_message(chat_id, help_text)
            return jsonify({"status": "ok"}), 200
        
        # /quiz command
        if text == '/quiz':
            if chat_id in user_quiz_sessions:
                del user_quiz_sessions[chat_id]
            
            session = QuizSession(chat_id)
            user_quiz_sessions[chat_id] = session
            
            first_q = session.get_current_question()
            options_text = "\n".join([f"{i+1}. {opt}" for i, opt in enumerate(first_q['options'])])
            
            quiz_intro = f"""
📚 *NIGERIA TAX QUIZ*

Test your knowledge of Nigerian taxes!

*Question 1 of {len(session.questions)}:*
{first_q['question']}

{options_text}

Send the number of your answer (1-4):
"""
            send_telegram_message(chat_id, quiz_intro)
            return jsonify({"status": "ok"}), 200
        
        # /learn command
        if text == '/learn':
            learn_menu = """
📚 *TAX LEARNING CENTER*

Select a topic:

/learnpaye - PAYE (Personal Income Tax)
/learncit - Company Income Tax
/learnvat - Value Added Tax

💡 *Each topic includes rates, rules, and examples!*
"""
            send_telegram_message(chat_id, learn_menu)
            return jsonify({"status": "ok"}), 200
        
        # /learnpaye command
        if text == '/learnpaye':
            material = get_tax_learning_material("paye")
            send_telegram_message(chat_id, material)
            return jsonify({"status": "ok"}), 200
        
        # /learncit command
        if text == '/learncit':
            material = get_tax_learning_material("cit")
            send_telegram_message(chat_id, material)
            return jsonify({"status": "ok"}), 200
        
        # /learnvat command
        if text == '/learnvat':
            material = get_tax_learning_material("vat")
            send_telegram_message(chat_id, material)
            return jsonify({"status": "ok"}), 200
        
        # /filepaye command
        if text == '/filepaye':
            guide = get_paye_filing_guide()
            send_telegram_message(chat_id, guide)
            return jsonify({"status": "ok"}), 200
        
        # /filecit command
        if text == '/filecit':
            guide = get_cit_filing_guide()
            send_telegram_message(chat_id, guide)
            return jsonify({"status": "ok"}), 200
        
        # /filevat command
        if text == '/filevat':
            guide = get_vat_filing_guide()
            send_telegram_message(chat_id, guide)
            return jsonify({"status": "ok"}), 200
        
        # /deadlines command
        if text == '/deadlines':
            paye_deadlines = get_filing_deadlines("paye")
            cit_deadlines = get_filing_deadlines("cit")
            vat_deadlines = get_filing_deadlines("vat")
            upcoming = get_upcoming_deadlines(14)
            upcoming_msg = format_deadline_message(upcoming)
            send_telegram_message(chat_id, f"{upcoming_msg}\n\n{paye_deadlines}\n\n{cit_deadlines}\n\n{vat_deadlines}")
            return jsonify({"status": "ok"}), 200
        
        # /contacts command
        if text == '/contacts':
            contacts = get_firs_contacts()
            send_telegram_message(chat_id, contacts)
            return jsonify({"status": "ok"}), 200
        
        # /penalties command
        if text == '/penalties':
            penalties = get_penalties_guide()
            send_telegram_message(chat_id, penalties)
            return jsonify({"status": "ok"}), 200
        
        # /gettin command
        if text == '/gettin':
            tin_guide = get_taxpayer_tin_guide()
            send_telegram_message(chat_id, tin_guide)
            return jsonify({"status": "ok"}), 200
        
        # /checklist command
        if text == '/checklist':
            keyboard = """
📋 *Select Tax Type*

/filechecklist paye - PAYE
/filechecklist cit - CIT
/filechecklist vat - VAT
"""
            send_telegram_message(chat_id, keyboard)
            return jsonify({"status": "ok"}), 200
        
        # /filechecklist command
        if text.startswith('/filechecklist '):
            parts = text.split()
            tax_type = parts[1].lower() if len(parts) > 1 else "paye"
            checklist = get_filing_checklist(tax_type)
            send_telegram_message(chat_id, checklist)
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
🏢 *VAT LIABILITY*

📥 Input VAT: ₦{data['input_vat']:,.2f}
📤 Output VAT: ₦{data['output_vat']:,.2f}

📊 Net: ₦{abs(data['net_liability']):,.2f} ({data['status']})
""")
                log_calculation(chat_id, "vat_liability", {"input_vat": input_vat, "output_vat": output_vat}, data)
            except (ValueError, IndexError):
                send_telegram_message(chat_id, "Example: `/vatliability 500000 750000`")
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
            send_telegram_message(chat_id, "Send a salary amount or use /help for commands.\n\n📚 *Try /quiz to test your tax knowledge!*")
        
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

Commands:

📊 /paye [amount] - PAYE tax
📊 /cit [turnover] - Company tax
📚 /quiz - Tax quiz
📚 /learn - Study materials
📋 /filepaye - Filing guide
👤 /history - Your history

Try /quiz to test your knowledge!"""
                    send_whatsapp_message(from_number, response)
            
            return jsonify({"status": "ok"}), 200
        except Exception as e:
            logging.error(f"WhatsApp error: {e}")
            return jsonify({"status": "error"}), 500

# ============ ADMIN BROADCAST ENDPOINT ============
@app.route('/api/admin/broadcast', methods=['POST'])
def admin_broadcast():
    try:
        data = request.get_json()
        admin_key = data.get('admin_key')
        message = data.get('message')
        platform = data.get('platform')
        
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
        
        if TEST_TELEGRAM_CHAT_ID and TELEGRAM_TOKEN:
            send_telegram_message(TEST_TELEGRAM_CHAT_ID, message)
        
        if TEST_WHATSAPP_NUMBER and WHATSAPP_ACCESS_TOKEN:
            send_whatsapp_message(TEST_WHATSAPP_NUMBER, message)
        
        all_users = get_all_active_users()
        broadcast_message(all_users, message, 'all')
        
        return jsonify({"status": "success", "deadlines_sent": len(deadlines)}), 200
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route('/api/cron/daily-tax-tip', methods=['POST', 'GET'])
def send_daily_tax_tip():
    try:
        tip = get_daily_tax_tip()
        message = f"{tip}\n\n📚 *Try /quiz to test your tax knowledge!*"
        
        if TEST_TELEGRAM_CHAT_ID and TELEGRAM_TOKEN:
            send_telegram_message(TEST_TELEGRAM_CHAT_ID, message)
        
        if TEST_WHATSAPP_NUMBER and WHATSAPP_ACCESS_TOKEN:
            send_whatsapp_message(TEST_WHATSAPP_NUMBER, message)
        
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