import os
import re
import logging
import uuid
import sys
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template_string
import requests
from dotenv import load_dotenv
from supabase import create_client, Client
from collections import defaultdict
import time

# Add parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# ============ LEGAL DISCLAIMERS (Context-Specific) ============
DISCLAIMER_MAIN = "🤖 *AI may make mistakes. Always verify with official sources.*"
DISCLAIMER_AI = "🤖 *AI-generated. Verify important information.*"
DISCLAIMER_CALC = "📊 *Estimate only. Actual tax may vary.*"
DISCLAIMER_FILING = "📋 *Record saved. Not an official filing with tax authorities.*"
DISCLAIMER_DOC = "📄 *For reference only. Not legally binding.*"
DISCLAIMER_CREDITS = "✅ *Transaction recorded. Contact support for issues.*"
DISCLAIMER_SUBSCRIPTION = "✅ *Subscription active. Auto-renews unless cancelled.*"

# ============ SUPABASE ============
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY") or os.getenv("SUPABASE_ANON_KEY")

supabase: Client = None

if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    logging.info("✅ Supabase client connected")
else:
    logging.error("❌ Supabase credentials missing!")

# ============ IMPORT SERVICES ============
try:
    from app.services.ask_service import ask_guarded
    SERVICES_AVAILABLE = True
    logging.info("✅ AI services imported successfully")
except Exception as e:
    logging.error(f"❌ Failed to import AI services: {e}")
    SERVICES_AVAILABLE = False

# ============ WHATSAPP ============
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "naija-tax-guide-verify")
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
WHATSAPP_API_URL = "https://graph.facebook.com/v18.0"

# ============ PAYSTACK ============
PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY")
PAYSTACK_API_URL = "https://api.paystack.co"

# Credit packages (ONLY for users with active subscription)
CREDIT_PACKAGES = {
    "T10": {"credits": 10, "amount_ngn": 500, "amount_kobo": 50000, "code": "T10", "description": "10 AI Credits", "requires_subscription": True},
    "T50": {"credits": 50, "amount_ngn": 2000, "amount_kobo": 200000, "code": "T50", "description": "50 AI Credits", "requires_subscription": True},
    "T100": {"credits": 100, "amount_ngn": 3500, "amount_kobo": 350000, "code": "T100", "description": "100 AI Credits", "requires_subscription": True},
    "T500": {"credits": 500, "amount_ngn": 15000, "amount_kobo": 1500000, "code": "T500", "description": "500 AI Credits", "requires_subscription": True},
}

# Feature credit costs
DEFAULT_CREDIT_COSTS = {
    "ai_question": 1,
    "paye_filing": 10,
    "vat_filing": 15,
    "cit_filing": 20,
    "document_simple": 5,
    "document_complex": 10
}

# Tax filing credit costs
TAX_FILING_COSTS = {
    "paye_assistance": 10,
    "vat_preparation": 15,
    "cit_filing": 20,
    "document_generation_simple": 5,
    "document_generation_complex": 10,
    "filing_summary": 5
}

# Free plan daily limits
FREE_PLAN_LIMITS = {
    "db_answers_daily": 50,
    "calculations_daily": 20
}

# Track user state
user_state = {}
user_cooldown = defaultdict(float)
daily_usage_cache = {}

# ============ DAILY USAGE TRACKING ============

def get_today_date():
    return datetime.now().date().isoformat()

def check_daily_limit(account_id, usage_type):
    """Check if user has exceeded daily free limit"""
    try:
        today = get_today_date()
        cache_key = f"{account_id}:{usage_type}:{today}"
        
        if cache_key in daily_usage_cache:
            current_usage = daily_usage_cache[cache_key]
        else:
            result = supabase.table("daily_free_usage").select(f"{usage_type}_used").eq("account_id", account_id).eq("usage_date", today).execute()
            if result.data:
                current_usage = result.data[0].get(f"{usage_type}_used", 0)
            else:
                current_usage = 0
            daily_usage_cache[cache_key] = current_usage
        
        if usage_type == "db_answers":
            limit = FREE_PLAN_LIMITS["db_answers_daily"]
        elif usage_type == "calculations":
            limit = FREE_PLAN_LIMITS["calculations_daily"]
        else:
            return True, 0
        
        if current_usage >= limit:
            return False, limit
        
        return True, limit
        
    except Exception as e:
        logging.error(f"Error checking daily limit: {e}")
        return True, 0

def increment_daily_usage(account_id, usage_type):
    """Increment daily usage counter"""
    try:
        today = get_today_date()
        cache_key = f"{account_id}:{usage_type}:{today}"
        
        current_usage = daily_usage_cache.get(cache_key, 0)
        daily_usage_cache[cache_key] = current_usage + 1
        
        existing = supabase.table("daily_free_usage").select("*").eq("account_id", account_id).eq("usage_date", today).execute()
        
        if existing.data:
            supabase.table("daily_free_usage").update({
                f"{usage_type}_used": current_usage + 1,
                "updated_at": datetime.now().isoformat()
            }).eq("account_id", account_id).eq("usage_date", today).execute()
        else:
            supabase.table("daily_free_usage").insert({
                "account_id": account_id,
                "usage_date": today,
                f"{usage_type}_used": 1,
                "db_answers_used": 0,
                "calculations_used": 0,
                "created_at": datetime.now().isoformat()
            }).execute()
        
        return True
    except Exception as e:
        logging.error(f"Error incrementing daily usage: {e}")
        return False

# ============ ACCOUNT MANAGEMENT ============

def get_canonical_account_id(phone_number):
    """Get or create canonical account_id"""
    if not supabase:
        logging.error("Supabase client not available")
        return None
    
    try:
        account_result = supabase.table("accounts").select("account_id").eq("provider_user_id", str(phone_number)).execute()
        
        if account_result.data:
            account_id = account_result.data[0].get("account_id")
            logging.info(f"Found existing account: {account_id}")
            return account_id
        
        user_result = supabase.table("bot_users").select("auth_user_id").eq("platform", "whatsapp").eq("user_id", str(phone_number)).execute()
        
        if user_result.data and user_result.data[0].get("auth_user_id"):
            auth_user_id = user_result.data[0].get("auth_user_id")
            existing_account = supabase.table("accounts").select("account_id").eq("account_id", auth_user_id).execute()
            if existing_account.data:
                return auth_user_id
            else:
                supabase.table("accounts").insert({
                    "account_id": auth_user_id,
                    "id": auth_user_id,
                    "provider": "whatsapp",
                    "provider_user_id": str(phone_number),
                    "phone": str(phone_number),
                    "created_at": datetime.now().isoformat(),
                    "updated_at": datetime.now().isoformat(),
                    "has_used_trial": False,
                    "language_preference": "en"
                }).execute()
                return auth_user_id
        
        auth_user_id = str(uuid.uuid4())
        
        supabase.table("bot_users").insert({
            "platform": "whatsapp",
            "user_id": str(phone_number),
            "auth_user_id": auth_user_id,
            "created_at": datetime.now().isoformat(),
            "total_calculations": 0,
            "is_active": True
        }).execute()
        
        supabase.table("accounts").insert({
            "account_id": auth_user_id,
            "id": auth_user_id,
            "provider": "whatsapp",
            "provider_user_id": str(phone_number),
            "phone": str(phone_number),
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "has_used_trial": False,
            "language_preference": "en"
        }).execute()
        
        supabase.table("ai_credit_balances").insert({
            "account_id": auth_user_id,
            "balance": 0,
            "plan_credits": 0,
            "topup_credits": 0,
            "updated_at": datetime.now().isoformat()
        }).execute()
        
        logging.info(f"✅ New user created: {auth_user_id}")
        return auth_user_id
        
    except Exception as e:
        logging.error(f"Error getting canonical account: {e}")
        return None

def get_active_subscription(account_id):
    """Get active subscription for user"""
    try:
        if not supabase:
            return None
        result = supabase.table("subscriptions").select("*").eq("account_id", account_id).eq("status", "active").execute()
        if result.data:
            sub = result.data[0]
            expires_at = sub.get("expires_at")
            if expires_at:
                try:
                    expiry = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
                    if expiry < datetime.now():
                        return None
                except:
                    pass
            return sub
        return None
    except Exception as e:
        logging.error(f"Error checking subscription: {e}")
        return None

def has_active_subscription(account_id):
    """Return True if user has active subscription"""
    return get_active_subscription(account_id) is not None

def get_credit_balance(account_id):
    """Get total credit balance (plan + topup)"""
    try:
        if supabase:
            result = supabase.table("ai_credit_balances").select("balance").eq("account_id", account_id).limit(1).execute()
            if result.data:
                return int(result.data[0].get("balance", 0))
            return 0
        return 0
    except Exception as e:
        logging.error(f"Error getting balance: {e}")
        return 0

def get_credit_details(account_id):
    """Get detailed credit information"""
    try:
        if supabase:
            result = supabase.table("ai_credit_balances").select("*").eq("account_id", account_id).limit(1).execute()
            if result.data:
                return result.data[0]
        return {"balance": 0, "plan_credits": 0, "topup_credits": 0}
    except Exception as e:
        logging.error(f"Error getting credit details: {e}")
        return {"balance": 0, "plan_credits": 0, "topup_credits": 0}

def deduct_credits(account_id, cost, feature_name):
    """Deduct credits from user's balance (top-up first, then plan)"""
    try:
        if not supabase:
            return False, "Service unavailable"
        
        credit_details = get_credit_details(account_id)
        current_topup = int(credit_details.get("topup_credits", 0))
        current_plan = int(credit_details.get("plan_credits", 0))
        current_balance = int(credit_details.get("balance", 0))
        
        if current_balance < cost:
            return False, f"Insufficient credits. Need {cost} credits, have {current_balance}. Buy top-up: T10, T50, T100, T500"
        
        if current_topup >= cost:
            new_topup = current_topup - cost
            new_balance = current_balance - cost
            supabase.table("ai_credit_balances").update({
                "balance": new_balance,
                "topup_credits": new_topup,
                "updated_at": datetime.now().isoformat()
            }).eq("account_id", account_id).execute()
            logging.info(f"Deducted {cost} credits from top-up for {feature_name}")
        else:
            remaining_cost = cost - current_topup
            new_topup = 0
            new_plan = current_plan - remaining_cost
            new_balance = current_balance - cost
            supabase.table("ai_credit_balances").update({
                "balance": new_balance,
                "topup_credits": 0,
                "plan_credits": new_plan,
                "updated_at": datetime.now().isoformat()
            }).eq("account_id", account_id).execute()
            logging.info(f"Deducted {cost} credits for {feature_name}: used {current_topup} top-up, {remaining_cost} plan")
        
        return True, f"Successfully used {cost} credits for {feature_name}"
        
    except Exception as e:
        logging.error(f"Error deducting credits: {e}")
        return False, str(e)

def add_topup_credits(account_id, credits, reference):
    """Add top-up credits to user's balance (ONLY for users with active subscription)"""
    if not has_active_subscription(account_id):
        return False, "You need an active subscription to buy top-up credits. Reply 4 to view plans."
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            if not supabase:
                return False, "Service unavailable"
            
            existing = supabase.table("ai_credit_balances").select("*").eq("account_id", account_id).execute()
            
            if existing.data:
                current_balance = int(existing.data[0].get("balance", 0))
                current_topup = int(existing.data[0].get("topup_credits", 0))
                
                new_topup = current_topup + int(credits)
                new_balance = current_balance + int(credits)
                
                supabase.table("ai_credit_balances").update({
                    "balance": new_balance,
                    "topup_credits": new_topup,
                    "updated_at": datetime.now().isoformat()
                }).eq("account_id", account_id).execute()
                
                logging.info(f"✅ Top-up: +{credits} credits. New balance: {new_balance}")
                return True, f"Successfully added {credits} top-up credits"
            else:
                supabase.table("ai_credit_balances").insert({
                    "account_id": account_id,
                    "balance": int(credits),
                    "plan_credits": 0,
                    "topup_credits": int(credits),
                    "updated_at": datetime.now().isoformat()
                }).execute()
                
                logging.info(f"✅ Top-up: Created new balance with {credits} credits")
                return True, f"Successfully added {credits} top-up credits"
                
        except Exception as e:
            logging.error(f"Attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(1)
            else:
                return False, str(e)

def get_credit_cost(feature, plan_code=None):
    return DEFAULT_CREDIT_COSTS.get(feature, 1)

def get_credit_packages_menu():
    return f"""💎 *Buy AI Credits*

⚠️ *Requires Active Subscription*

Reply with any code to buy top-up credits:

T10 - 10 credits - ₦500
T50 - 50 credits - ₦2,000
T100 - 100 credits - ₦3,500
T500 - 500 credits - ₦15,000

{DISCLAIMER_CREDITS}

0 - Cancel | # - Main Menu"""

# ============ TAX CALCULATION (Free feature via CALC command) ============
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

def calculate_vat(amount, rate=7.5):
    """Calculate VAT"""
    vat = amount * rate / 100
    total = amount + vat
    return {
        "amount": amount,
        "rate": rate,
        "vat": round(vat, 2),
        "total": round(total, 2)
    }

def calculate_cit(revenue, expenses, allowances=0):
    """Calculate Company Income Tax"""
    profit = revenue - expenses - allowances
    if revenue > 100000000:
        rate = 30
    elif revenue > 25000000:
        rate = 20
    else:
        rate = 0
    cit = profit * rate / 100
    education_tax = profit * 3 / 100 if revenue > 25000000 else 0
    return {
        "revenue": revenue,
        "expenses": expenses,
        "profit": profit,
        "rate": rate,
        "cit": round(cit, 2),
        "education_tax": round(education_tax, 2),
        "total": round(cit + education_tax, 2)
    }

def handle_calc_command(phone_number, text, account_id):
    """Handle CALC command for tax calculations (free feature)"""
    calc_text = text[4:].strip().upper()
    
    # Help command
    if calc_text == "" or calc_text == "HELP":
        return f"""📊 *Tax Calculator*

Examples:
• CALC PAYE 500000 - Calculate PAYE tax
• CALC 500000 - Same as above
• CALC VAT 100000 - Calculate VAT at 7.5%
• CALC CIT 50000000 20000000 - CIT with revenue & expenses

{DISCLAIMER_CALC}

For official filing, press 7 then F1-F3"""
    
    # PAYE Calculation
    if calc_text.startswith("PAYE"):
        amount_str = calc_text.replace("PAYE", "").strip()
        try:
            amount = float(amount_str.replace(',', ''))
            data = calculate_paye(amount)
            return f"""📊 *PAYE CALCULATION RESULT*

Gross: ₦{data['gross']:,.0f}
Pension (8%): ₦{data['pension']:,.0f}
NHF (2.5%): ₦{data['nhf']:,.0f}
Tax: ₦{data['tax']:,.0f}
Net: *₦{data['net']:,.0f}*
Effective Rate: {data['rate']}%

{DISCLAIMER_CALC}"""
        except:
            return "❌ Invalid amount. Example: CALC PAYE 500000"
    
    # VAT Calculation
    if calc_text.startswith("VAT"):
        amount_str = calc_text.replace("VAT", "").strip()
        try:
            amount = float(amount_str.replace(',', ''))
            data = calculate_vat(amount)
            return f"""📊 *VAT CALCULATION RESULT*

Amount (excl. VAT): ₦{data['amount']:,.2f}
VAT Rate: {data['rate']}%
VAT Amount: ₦{data['vat']:,.2f}
Total (incl. VAT): *₦{data['total']:,.2f}*

{DISCLAIMER_CALC}"""
        except:
            return "❌ Invalid amount. Example: CALC VAT 100000"
    
    # CIT Calculation
    if calc_text.startswith("CIT"):
        parts = calc_text.replace("CIT", "").strip().split()
        if len(parts) >= 2:
            try:
                revenue = float(parts[0].replace(',', ''))
                expenses = float(parts[1].replace(',', ''))
                allowances = float(parts[2].replace(',', '')) if len(parts) >= 3 else 0
                data = calculate_cit(revenue, expenses, allowances)
                
                size = "Large" if revenue > 100000000 else "Medium" if revenue > 25000000 else "Small (Exempt)"
                return f"""📊 *CIT CALCULATION RESULT*

Company Size: {size}
Revenue: ₦{data['revenue']:,.2f}
Expenses: ₦{data['expenses']:,.2f}
Taxable Profit: ₦{data['profit']:,.2f}
CIT Rate: {data['rate']}%
CIT Payable: ₦{data['cit']:,.2f}
Education Tax: ₦{data['education_tax']:,.2f}
*Total Tax Due: ₦{data['total']:,.2f}*

{DISCLAIMER_CALC}"""
            except:
                return "❌ Invalid format. Example: CALC CIT 50000000 20000000"
        else:
            return "❌ Need revenue and expenses. Example: CALC CIT 50000000 20000000"
    
    # Simple number - assume PAYE
    try:
        amount = float(calc_text.replace(',', ''))
        data = calculate_paye(amount)
        return f"""📊 *PAYE CALCULATION RESULT*

Gross: ₦{data['gross']:,.0f}
Pension: ₦{data['pension']:,.0f}
NHF: ₦{data['nhf']:,.0f}
Tax: ₦{data['tax']:,.0f}
Net: *₦{data['net']:,.0f}*
Rate: {data['rate']}%

{DISCLAIMER_CALC}"""
    except:
        return "❌ Invalid command. Type CALC HELP for examples."

# ============ TAX FILING HISTORY ============

def save_filing_record(account_id, filing_type, reference, inputs, result_summary, credits_used=0):
    """Save filing record to existing tax_filings table"""
    try:
        if not supabase:
            logging.error("Supabase client not available")
            return False
        
        now_iso = datetime.now().isoformat()
        
        supabase.table("tax_filings").insert({
            "user_id": account_id,
            "tax_type": filing_type,
            "inputs": inputs,
            "status": "submitted",
            "reference": reference,
            "submitted_at": now_iso,
            "filing_reference": reference,
            "result_summary": result_summary[:500] if result_summary else None,
            "credits_used": credits_used,
            "channel": "whatsapp",
            "updated_at": now_iso
        }).execute()
        logging.info(f"✅ Filing saved: {reference} for user {account_id}")
        return True
            
    except Exception as e:
        logging.error(f"Error saving filing: {e}")
        return False

def get_filing_history(account_id):
    """Get user's filing history from existing tax_filings table"""
    try:
        if not supabase:
            return "Unable to retrieve filing history."
        
        result = supabase.table("tax_filings")\
            .select("filing_reference, tax_type, credits_used, status, submitted_at, channel")\
            .eq("user_id", account_id)\
            .eq("channel", "whatsapp")\
            .order("submitted_at", desc=True)\
            .limit(10)\
            .execute()
        
        if not result.data:
            return "📋 *Filing History*\n\nNo filings found.\n\nStart a filing with 7 then F1-F4."
        
        history = "📋 *Filing History*\n\n"
        for filing in result.data[:5]:
            filing_type = filing.get("tax_type", "Unknown")
            reference = filing.get("filing_reference", "N/A")
            status = filing.get("status", "submitted")
            submitted_at = filing.get("submitted_at", "")[:10] if filing.get("submitted_at") else "Unknown"
            credits_used = filing.get("credits_used", 0)
            history += f"• *{filing_type}*: {reference}\n  📅 {submitted_at} | 💳 {credits_used} credits | {status}\n\n"
        
        history += f"\n{DISCLAIMER_FILING}\n\nReply 7 then F5 to refresh."
        return history
    except Exception as e:
        logging.error(f"Error getting filing history: {e}")
        return "Error retrieving filing history. Please try again."

# ============ TAX FILING & MANAGEMENT ============

def get_filing_menu():
    return f"""📋 *TAX FILING & MANAGEMENT*

⚠️ *Premium Feature* (Requires Active Subscription)

First press 7, then use these codes:

F1 - PAYE Filing Assistance (10 credits)
F2 - VAT Return Preparation (15 credits)
F3 - CIT Calculation & Filing (20 credits)
F4 - Generate Document (5-10 credits)
F5 - View Filing History
F0 - Back to Main Menu

💡 Example: Type 7 then F1 to start PAYE filing
{DISCLAIMER_FILING}

0 - Cancel | # - Main Menu"""

def get_paye_filing_questions(step, inputs=None):
    steps = {
        1: "📋 *PAYE Filing - Step 1/5*\n\nEnter employee's monthly salary:\n(Example: 500000)\n\n0 - Cancel | # - Menu",
        2: "📋 *PAYE Filing - Step 2/5*\n\nEnter pension contribution (employee):\n(Example: 40000 or 0 if none)\n\n0 - Cancel | # - Menu",
        3: "📋 *PAYE Filing - Step 3/5*\n\nEnter NHF contribution (employee):\n(Example: 12500 or 0 if none)\n\n0 - Cancel | # - Menu",
        4: "📋 *PAYE Filing - Step 4/5*\n\nEnter other allowances (if any):\n(Example: 50000 or 0)\n\n0 - Cancel | # - Menu",
        5: "📋 *PAYE Filing - Step 5/5*\n\nEnter tax reliefs (if any):\n(Example: 200000 or 0)\n\n0 - Cancel | # - Menu"
    }
    return steps.get(step, "Invalid step. Start over with 7 then F1.")

def process_paye_filing(inputs):
    salary = float(inputs.get("salary", 0))
    pension = float(inputs.get("pension", 0))
    nhf = float(inputs.get("nhf", 0))
    allowances = float(inputs.get("allowances", 0))
    reliefs = float(inputs.get("reliefs", 200000))
    
    annual_gross = (salary + allowances) * 12
    annual_pension = pension * 12
    annual_nhf = nhf * 12
    
    cra_fixed = reliefs
    cra_one_percent = annual_gross * 0.01
    cra_base = max(cra_fixed, cra_one_percent)
    cra_percentage = annual_gross * 0.20
    cra_total = cra_base + cra_percentage
    
    total_deductions = annual_pension + annual_nhf + cra_total
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
    
    monthly_tax = annual_tax / 12
    
    return f"""📋 *PAYE FILING SUMMARY*

📊 *Employee Details:*
Monthly Salary: ₦{salary:,.2f}
Pension: ₦{pension:,.2f}
NHF: ₦{nhf:,.2f}
Allowances: ₦{allowances:,.2f}

💰 *Tax Calculation:*
Annual Gross: ₦{annual_gross:,.2f}
Chargeable Income: ₦{chargeable:,.2f}
Annual Tax: ₦{annual_tax:,.2f}
*Monthly Tax: ₦{monthly_tax:,.2f}*

📝 *Filing Reference:* PAYE_{datetime.now().strftime('%Y%m%d')}_{uuid.uuid4().hex[:6]}

{DISCLAIMER_FILING}

Reply 8 for main menu or 7 then F1-F4 for more filing"""

def get_vat_filing_questions(step, inputs=None):
    steps = {
        1: "📋 *VAT Filing - Step 1/4*\n\nEnter total sales for the period:\n(Example: 5000000)\n\n0 - Cancel | # - Menu",
        2: "📋 *VAT Filing - Step 2/4*\n\nEnter total purchases (excluding VAT):\n(Example: 3000000)\n\n0 - Cancel | # - Menu",
        3: "📋 *VAT Filing - Step 3/4*\n\nEnter VAT rate (default 7.5%):\n(Reply 1 for 7.5%, or enter custom rate)\n\n0 - Cancel | # - Menu",
        4: "📋 *VAT Filing - Step 4/4*\n\nEnter filing period (month/year):\n(Example: May 2026)\n\n0 - Cancel | # - Menu"
    }
    return steps.get(step, "Invalid step. Start over with 7 then F2.")

def process_vat_filing(inputs):
    sales = float(inputs.get("sales", 0))
    purchases = float(inputs.get("purchases", 0))
    vat_rate = float(inputs.get("vat_rate", 7.5)) / 100
    period = inputs.get("period", datetime.now().strftime("%B %Y"))
    
    output_vat = sales * vat_rate
    input_vat = purchases * vat_rate
    vat_payable = max(0, output_vat - input_vat)
    
    return f"""📋 *VAT FILING SUMMARY*

📊 *Period:* {period}

💰 *Transaction Details:*
Total Sales: ₦{sales:,.2f}
Output VAT: ₦{output_vat:,.2f}
Total Purchases: ₦{purchases:,.2f}
Input VAT: ₦{input_vat:,.2f}

📝 *VAT Payable:*
*₦{vat_payable:,.2f}*

📋 *Filing Reference:* VAT_{datetime.now().strftime('%Y%m%d')}_{uuid.uuid4().hex[:6]}

{DISCLAIMER_FILING}

Reply 8 for main menu or 7 then F1-F4 for more filing"""

def get_cit_filing_questions(step, inputs=None):
    steps = {
        1: "📋 *CIT Filing - Step 1/5*\n\nEnter company's total revenue for the year:\n(Example: 50000000)\n\n0 - Cancel | # - Menu",
        2: "📋 *CIT Filing - Step 2/5*\n\nEnter cost of sales:\n(Example: 25000000)\n\n0 - Cancel | # - Menu",
        3: "📋 *CIT Filing - Step 3/5*\n\nEnter operating expenses:\n(Example: 10000000)\n\n0 - Cancel | # - Menu",
        4: "📋 *CIT Filing - Step 4/5*\n\nEnter capital allowances:\n(Example: 2000000)\n\n0 - Cancel | # - Menu",
        5: "📋 *CIT Filing - Step 5/5*\n\nEnter tax year:\n(Example: 2026)\n\n0 - Cancel | # - Menu"
    }
    return steps.get(step, "Invalid step. Start over with 7 then F3.")

def process_cit_filing(inputs):
    revenue = float(inputs.get("revenue", 0))
    cost_of_sales = float(inputs.get("cost_of_sales", 0))
    expenses = float(inputs.get("expenses", 0))
    allowances = float(inputs.get("allowances", 0))
    tax_year = inputs.get("tax_year", str(datetime.now().year))
    
    gross_profit = revenue - cost_of_sales
    taxable_profit = max(0, gross_profit - expenses - allowances)
    
    if revenue > 100000000:
        cit_rate = 0.30
        company_size = "Large"
    elif revenue > 25000000:
        cit_rate = 0.20
        company_size = "Medium"
    else:
        cit_rate = 0.00
        company_size = "Small (Exempt)"
    
    cit_payable = taxable_profit * cit_rate
    education_tax = taxable_profit * 0.03 if revenue > 25000000 else 0
    total_tax = cit_payable + education_tax
    
    return f"""📋 *CIT FILING SUMMARY*

🏢 *Company Details:*
Size: {company_size}
Tax Year: {tax_year}

📊 *Financial Summary:*
Revenue: ₦{revenue:,.2f}
Cost of Sales: ₦{cost_of_sales:,.2f}
Gross Profit: ₦{gross_profit:,.2f}
Operating Expenses: ₦{expenses:,.2f}
Capital Allowances: ₦{allowances:,.2f}
Taxable Profit: ₦{taxable_profit:,.2f}

💰 *Tax Calculation:*
CIT Rate: {cit_rate*100}%
CIT Payable: ₦{cit_payable:,.2f}
Education Tax: ₦{education_tax:,.2f}
*Total Tax Due: ₦{total_tax:,.2f}*

📋 *Filing Reference:* CIT_{tax_year}_{uuid.uuid4().hex[:6]}

{DISCLAIMER_FILING}

Reply 8 for main menu or 7 then F1-F4 for more filing"""

def get_document_generation_menu():
    return f"""📄 *Document Generation*

Select document type:

F4-1 - Tax Payment Receipt (5 credits)
F4-2 - PAYE Filing Form (5 credits)
F4-3 - VAT Return Form (5 credits)
F4-4 - CIT Computation Report (10 credits)
F4-5 - Annual Tax Summary (10 credits)
F0 - Back to Filing Menu

{DISCLAIMER_DOC}

0 - Cancel | # - Main Menu"""

def process_document_generation(doc_type, account_id, user_data):
    doc_types = {
        "1": {"name": "Tax Payment Receipt", "cost": 5},
        "2": {"name": "PAYE Filing Form", "cost": 5},
        "3": {"name": "VAT Return Form", "cost": 5},
        "4": {"name": "CIT Computation Report", "cost": 10},
        "5": {"name": "Annual Tax Summary", "cost": 10}
    }
    
    doc_info = doc_types.get(doc_type)
    if not doc_info:
        return None, "Invalid document type"
    
    doc_ref = f"DOC_{doc_info['name'].replace(' ', '_')}_{uuid.uuid4().hex[:8]}"
    
    result = f"""📄 *Document Generated*

📋 Type: {doc_info['name']}
🆔 Reference: {doc_ref}
📅 Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}
💳 Credits Used: {doc_info['cost']}

📎 *Document Summary:* Your {doc_info['name']} has been generated.

{DISCLAIMER_DOC}

Reply 8 for main menu or 7 then F4 for more documents"""
    
    return doc_ref, result

# ============ PREMIUM FEATURE HANDLERS ============

def handle_ai_question(account_id, question):
    if not has_active_subscription(account_id):
        return f"""❌ *Premium Feature*

AI answers require an active subscription.

📋 *To access AI answers:*
1. Subscribe to a plan (Reply 4)
2. Get monthly credits
3. Each AI answer = 1 credit

{DISCLAIMER_AI}

Reply 4 to view plans""", False
    
    cost = get_credit_cost("ai_question")
    credit_details = get_credit_details(account_id)
    current_balance = int(credit_details.get("balance", 0))
    
    if current_balance < cost:
        return f"""❌ *Insufficient Credits*

Need {cost} credit for AI answer.
Current balance: {current_balance} credits

Options:
1. Buy top-up credits: T10, T50, T100, T500
2. Check balance: Reply 2

{DISCLAIMER_AI}""", False
    
    success, message = deduct_credits(account_id, cost, "AI question")
    if not success:
        return f"❌ {message}", False
    
    try:
        result = ask_guarded({
            "question": question,
            "account_id": account_id,
            "lang": "en",
            "channel": "whatsapp"
        })
        
        if result.get("ok"):
            answer = result.get("answer", "")
            new_balance = get_credit_balance(account_id)
            return f"{answer}\n\n---\n💎 *Credits remaining:* {new_balance}\n\n{DISCLAIMER_AI}\n\nReply 1 for another question or 8 for menu.", True
        else:
            add_topup_credits(account_id, cost, f"REFUND_{uuid.uuid4().hex[:8]}")
            return f"❌ AI service error: {result.get('error', 'Unknown error')}\n\nCredits have been refunded.\n{DISCLAIMER_AI}", False
    except Exception as e:
        add_topup_credits(account_id, cost, f"REFUND_{uuid.uuid4().hex[:8]}")
        return f"❌ Error: {str(e)}\n\nCredits have been refunded.\n{DISCLAIMER_AI}", False

def handle_database_answer(account_id, question):
    if not has_active_subscription(account_id):
        allowed, limit = check_daily_limit(account_id, "db_answers")
        if not allowed:
            return f"""📚 *Daily Limit Reached*

You have reached your daily limit of {limit} database answers.

Subscribe to unlock unlimited answers + AI features.

Reply 4 to view plans""", False
    
    return None, False

# ============ SUBSCRIPTION PLANS ============
def get_all_plans():
    try:
        if supabase:
            result = supabase.table("plans").select("*").eq("active", True).execute()
            return result.data or []
        return []
    except Exception as e:
        logging.error(f"Error fetching plans: {e}")
        return []

def get_user_subscription(phone_number):
    try:
        canonical_account_id = get_canonical_account_id(phone_number)
        if not canonical_account_id or not supabase:
            return None
        
        sub_result = supabase.table("subscriptions").select("*").eq("account_id", canonical_account_id).eq("status", "active").order("created_at", desc=True).limit(1).execute()
        
        if sub_result.data:
            return sub_result.data[0]
        return None
    except Exception as e:
        logging.error(f"Error getting subscription: {e}")
        return None

def format_subscription_message(subscription, plan, credit_details):
    if not subscription:
        return f"""📋 *NO ACTIVE SUBSCRIPTION*

You are on the Free Plan.

📊 *Free Plan Limits (daily):*
• Database answers: 50
• Calculations: 20 (use CALC command)
• AI answers: 0 (requires subscription)
• Premium features: 0

💡 *To access premium features:*
Reply 4 to view subscription plans

{DISCLAIMER_MAIN}"""
    
    plan_name = plan.get("name", "Unknown") if plan else subscription.get("plan", "Unknown")
    amount = subscription.get("amount", 0)
    created_at = subscription.get("created_at", "")
    status = subscription.get("status", "active")
    plan_credits = plan.get("ai_credits_total", 0) if plan else 0
    
    created_date = created_at[:10] if created_at else "Unknown"
    total_balance = int(credit_details.get("balance", 0))
    topup_credits = int(credit_details.get("topup_credits", 0))
    plan_credits_remaining = int(credit_details.get("plan_credits", 0))
    
    expires_at = subscription.get("expires_at")
    days_remaining = ""
    if expires_at:
        try:
            expiry = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
            remaining = expiry - datetime.now()
            days = remaining.days
            if days > 0:
                days_remaining = f"\n📅 Days remaining: {days}"
        except:
            pass
    
    return f"""📋 *YOUR SUBSCRIPTION*

✅ Plan: {plan_name}
💰 Amount: ₦{amount:,.2f}
🎯 Monthly Credits: {plan_credits}
📅 Activated: {created_date}{days_remaining}
📊 Status: {status.upper()}
🔄 Auto-renew: ON

📊 *Credit Balance:*
• Total available: {total_balance} credits
• Top-up credits: {topup_credits} (used first)
• Plan credits: {plan_credits_remaining}

💡 *Premium Features:*
• AI questions: 1 credit
• PAYE filing: 10 credits
• VAT filing: 15 credits
• CIT filing: 20 credits
• Document generation: 5-10 credits

{DISCLAIMER_SUBSCRIPTION}

To buy top-up credits: Type T10, T50, T100, or T500"""

def get_plans_list_menu():
    try:
        plans = get_all_plans()
        
        if not plans:
            return "📋 *Subscription Plans*\n\nNo plans available at the moment."
        
        menu_lines = ["📋 *AVAILABLE SUBSCRIPTION PLANS*\n", "⚠️ *Required for premium features*\n"]
        
        starter_plans = [p for p in plans if "starter" in p.get("plan_code", "")]
        professional_plans = [p for p in plans if "professional" in p.get("plan_code", "")]
        business_plans = [p for p in plans if "business" in p.get("plan_code", "")]
        
        def get_billing(plan_code):
            if "yearly" in plan_code:
                return "yearly"
            elif "quarterly" in plan_code:
                return "quarterly"
            return "monthly"
        
        def sort_by_billing(plan_list):
            order = {"monthly": 0, "quarterly": 1, "yearly": 2}
            return sorted(plan_list, key=lambda x: order.get(get_billing(x.get("plan_code", "")), 99))
        
        code_display = {
            "starter_monthly": "S1",
            "starter_quarterly": "S2",
            "starter_yearly": "S3",
            "professional_monthly": "P1",
            "professional_quarterly": "P2",
            "professional_yearly": "P3",
            "business_monthly": "B1",
            "business_quarterly": "B2",
            "business_yearly": "B3"
        }
        
        if starter_plans:
            menu_lines.append("*STARTER PLANS*")
            for plan in sort_by_billing(starter_plans):
                name = plan.get("name", "Unknown")
                price = plan.get("price", 0)
                credits = plan.get("ai_credits_total", 0)
                plan_code = plan.get("plan_code", "")
                billing = get_billing(plan_code)
                billing_display = {"monthly": "month", "quarterly": "quarter", "yearly": "year"}.get(billing, billing)
                short_code = code_display.get(plan_code, "")
                menu_lines.append(f"  • *{name}* - ₦{price:,}/{billing_display} - {credits} credits/month")
                menu_lines.append(f"    (Code: {short_code})")
            menu_lines.append("")
        
        if professional_plans:
            menu_lines.append("*PROFESSIONAL PLANS*")
            for plan in sort_by_billing(professional_plans):
                name = plan.get("name", "Unknown")
                price = plan.get("price", 0)
                credits = plan.get("ai_credits_total", 0)
                plan_code = plan.get("plan_code", "")
                billing = get_billing(plan_code)
                billing_display = {"monthly": "month", "quarterly": "quarter", "yearly": "year"}.get(billing, billing)
                short_code = code_display.get(plan_code, "")
                menu_lines.append(f"  • *{name}* - ₦{price:,}/{billing_display} - {credits} credits/month")
                menu_lines.append(f"    (Code: {short_code})")
            menu_lines.append("")
        
        if business_plans:
            menu_lines.append("*BUSINESS PLANS*")
            for plan in sort_by_billing(business_plans):
                name = plan.get("name", "Unknown")
                price = plan.get("price", 0)
                credits = plan.get("ai_credits_total", 0)
                plan_code = plan.get("plan_code", "")
                billing = get_billing(plan_code)
                billing_display = {"monthly": "month", "quarterly": "quarter", "yearly": "year"}.get(billing, billing)
                short_code = code_display.get(plan_code, "")
                menu_lines.append(f"  • *{name}* - ₦{price:,}/{billing_display} - {credits} credits/month")
                menu_lines.append(f"    (Code: {short_code})")
            menu_lines.append("")
        
        menu_lines.append("💡 *Premium Features (require active plan):*")
        menu_lines.append("• AI answers: 1 credit")
        menu_lines.append("• PAYE filing: 10 credits")
        menu_lines.append("• VAT filing: 15 credits")
        menu_lines.append("• CIT filing: 20 credits")
        menu_lines.append("• Document generation: 5-10 credits")
        menu_lines.append("")
        menu_lines.append(DISCLAIMER_MAIN)
        menu_lines.append("")
        menu_lines.append("0 - Cancel | # - Main Menu")
        
        return "\n".join(menu_lines)
    except Exception as e:
        logging.error(f"Error fetching plans: {e}")
        return "📋 *Subscription Plans*\n\nPlease visit www.naijataxguides.com/plans"

def get_main_menu():
    return f"""*🤖 Naija Tax Guide*

Reply with:

1️⃣ - Ask a tax question
2️⃣ - Check credits balance
3️⃣ - Check my subscription
4️⃣ - View subscription plans
5️⃣ - Premium features
6️⃣ - Buy top-up credits
7️⃣ - Tax filing & management
8️⃣ - Help / Menu

---
*Free Features:*
• CALC 500000 - Calculate PAYE tax
• CALC VAT 100000 - Calculate VAT
• CALC CIT 50000000 20000000 - Calculate CIT
• Database answers (50/day)

*Premium Features (require subscription):*
• AI answers (1 credit)
• PAYE filing (10 credits)
• VAT filing (15 credits)
• CIT filing (20 credits)
• Document generation (5-10 credits)

*Quick Commands:*
T10, T50, T100, T500 - Buy top-up (requires subscription)
CALC - Tax calculator (free)

*Filing Commands (press 7 first):*
F1, F2, F3, F4, F5

*Global commands:*
# - Menu | * - Back | 0 - Cancel

{DISCLAIMER_MAIN}"""

def send_whatsapp(to_phone, text):
    try:
        url = f"{WHATSAPP_API_URL}/{PHONE_NUMBER_ID}/messages"
        headers = {"Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}", "Content-Type": "application/json"}
        payload = {"messaging_product": "whatsapp", "to": to_phone, "type": "text", "text": {"body": text}}
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        if response.status_code == 200:
            logging.info(f"Sent to {to_phone}")
            return True
        else:
            logging.error(f"Failed to send to {to_phone}: {response.status_code}")
        return False
    except Exception as e:
        logging.error(f"Send error: {e}")
        return False

# ============ CALLBACK PAGE HTML ============
SUCCESS_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Payment Successful - Naija Tax Guide</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            margin: 0;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        }
        .container {
            text-align: center;
            background: white;
            padding: 40px;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            max-width: 90%;
            width: 400px;
        }
        .success-icon {
            width: 80px;
            height: 80px;
            background: #4CAF50;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            margin: 0 auto 20px;
        }
        .success-icon svg {
            width: 50px;
            height: 50px;
            fill: white;
        }
        h1 {
            color: #333;
            margin-bottom: 10px;
        }
        p {
            color: #666;
            margin-bottom: 20px;
        }
        .plan-name {
            background: #f0f0f0;
            padding: 10px;
            border-radius: 10px;
            margin: 20px 0;
            font-weight: bold;
        }
        .redirect-timer {
            color: #999;
            font-size: 14px;
            margin-top: 20px;
        }
        .manual-link {
            margin-top: 15px;
        }
        .manual-link a {
            color: #667eea;
            text-decoration: none;
        }
        .whatsapp-button {
            display: inline-block;
            background: #25D366;
            color: white;
            padding: 12px 24px;
            border-radius: 30px;
            text-decoration: none;
            margin-top: 20px;
            font-weight: bold;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="success-icon">
            <svg viewBox="0 0 24 24">
                <path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41L9 16.17z"/>
            </svg>
        </div>
        <h1>✅ Payment Successful!</h1>
        <p>Your {{ type }} has been processed successfully.</p>
        <div class="plan-name">🎯 {{ amount_info }}</div>
        <div class="redirect-timer">Redirecting to WhatsApp in <span id="countdown">3</span> seconds...</div>
        <div class="manual-link">
            <a href="{{ whatsapp_url }}" class="whatsapp-button">💬 Return to WhatsApp Now</a>
        </div>
    </div>
    <script>
        let seconds = 3;
        const countdownElement = document.getElementById('countdown');
        const whatsappUrl = "{{ whatsapp_url }}";
        
        const timer = setInterval(function() {
            seconds--;
            countdownElement.textContent = seconds;
            if (seconds <= 0) {
                clearInterval(timer);
                window.location.href = whatsappUrl;
            }
        }, 1000);
    </script>
</body>
</html>
"""

# ============ FLASK ENDPOINTS ============

@app.route('/health', methods=['GET'])
def health():
    return "OK"

@app.route('/payment/success', methods=['GET'])
def payment_success():
    phone = request.args.get('phone', '')
    payment_type = request.args.get('type', 'subscription')
    plan_name = request.args.get('plan', '')
    credits = request.args.get('credits', '')
    
    clean_phone = re.sub(r'\D', '', phone)
    if len(clean_phone) == 13 and clean_phone.startswith('234'):
        clean_phone = clean_phone[3:]
    
    whatsapp_url = f"https://wa.me/{clean_phone}"
    
    if payment_type == 'credits':
        amount_info = f"{credits} AI Credits added!"
    else:
        amount_info = plan_name
    
    return render_template_string(SUCCESS_PAGE, type=payment_type, amount_info=amount_info, whatsapp_url=whatsapp_url)

@app.route('/api/billing/webhook', methods=['POST'])
def billing_webhook():
    """Handle Paystack webhook"""
    try:
        payload = request.get_json()
        if not payload:
            return "No payload", 400
        
        event = payload.get('event')
        data = payload.get('data', {})
        
        logging.info(f"📨 Billing webhook received: {event}")
        
        if event == 'charge.success':
            metadata = data.get('metadata', {})
            transaction_type = metadata.get('type', 'subscription')
            reference = data.get('reference')
            
            try:
                if supabase:
                    existing_tx = supabase.table("paystack_transactions").select("status").eq("reference", reference).execute()
                    if existing_tx.data and existing_tx.data[0].get("status") == 'success':
                        logging.info(f"⏭️ Transaction {reference} already processed.")
                        return "OK - Already Processed", 200
            except Exception:
                pass
            
            if transaction_type == 'credit_purchase':
                account_id = metadata.get('account_id')
                credits_raw = metadata.get('credits', 0)
                phone_number = metadata.get('provider_user_id')
                amount = data.get('amount', 0) / 100
                
                try:
                    credits = int(credits_raw)
                except (TypeError, ValueError):
                    credits = 0
                
                if account_id and credits > 0:
                    success, message = add_topup_credits(account_id, credits, reference)
                    
                    if success and phone_number:
                        try:
                            if supabase:
                                supabase.table("paystack_transactions").update({
                                    "status": "success",
                                    "updated_at": datetime.now().isoformat()
                                }).eq("reference", reference).execute()
                        except Exception:
                            pass
                        
                        time.sleep(1)
                        credit_details = get_credit_details(account_id)
                        total_balance = int(credit_details.get("balance", 0))
                        topup_credits = int(credit_details.get("topup_credits", 0))
                        plan_credits = int(credit_details.get("plan_credits", 0))
                        
                        confirmation_msg = f"""✅ *CREDITS ADDED SUCCESSFULLY!*

💎 *{credits} top-up credits* added to your account.

💰 Amount: ₦{amount:,.2f}
🆔 Reference: {reference}

📊 *Current Balance:*
• Total: *{total_balance}* credits
• Top-up: {topup_credits} (used first)
• Plan: {plan_credits}

💡 Each credit = 1 AI question or premium action

{DISCLAIMER_CREDITS}

Reply 1 for tax questions or 8 for menu."""
                        
                        send_whatsapp(phone_number, confirmation_msg)
                        logging.info(f"✅ Top-up completed: +{credits} credits")
                    else:
                        logging.error(f"❌ Failed to add credits: {message}")
                        if phone_number:
                            send_whatsapp(phone_number, f"⚠️ {message}\n\nReference: {reference}\n\nReply 8 for menu.")
            
            elif transaction_type == 'subscription':
                phone_number = metadata.get('phone')
                plan_name = metadata.get('plan_name', 'Subscription')
                amount = data.get('amount', 0) / 100
                plan_code = metadata.get('plan_code')
                
                if phone_number:
                    confirmation_msg = f"""✅ *PAYMENT SUCCESSFUL!*

🎉 Thank you for subscribing!

📋 Plan: {plan_name}
💰 Amount: ₦{amount:,.2f}
🆔 Reference: {reference}

{DISCLAIMER_SUBSCRIPTION}

Reply 8 for menu."""
                    
                    send_whatsapp(phone_number, confirmation_msg)
                    
                    try:
                        canonical_account_id = get_canonical_account_id(phone_number)
                        if canonical_account_id and supabase:
                            plan_details = None
                            for p in get_all_plans():
                                if p.get("plan_code") == plan_code:
                                    plan_details = p
                                    break
                            
                            plan_credits = plan_details.get("ai_credits_total", 0) if plan_details else 0
                            duration_days = plan_details.get("duration_days", 30) if plan_details else 30
                            expires_at = datetime.now() + timedelta(days=duration_days)
                            
                            existing_sub = supabase.table("subscriptions").select("*").eq("account_id", canonical_account_id).eq("plan_code", plan_code).execute()
                            
                            if not existing_sub.data:
                                supabase.table("subscriptions").insert({
                                    "account_id": canonical_account_id,
                                    "user_id": canonical_account_id,
                                    "plan_code": plan_code,
                                    "plan": plan_code,
                                    "status": "active",
                                    "paystack_ref": reference,
                                    "amount": float(amount),
                                    "amount_kobo": int(amount * 100),
                                    "currency": "NGN",
                                    "expires_at": expires_at.isoformat(),
                                    "created_at": datetime.now().isoformat(),
                                    "updated_at": datetime.now().isoformat()
                                }).execute()
                                logging.info(f"✅ Subscription activated: {plan_name}")
                                
                                existing_balance = supabase.table("ai_credit_balances").select("*").eq("account_id", canonical_account_id).execute()
                                if existing_balance.data:
                                    current_topup = int(existing_balance.data[0].get("topup_credits", 0))
                                    new_balance = plan_credits + current_topup
                                    supabase.table("ai_credit_balances").update({
                                        "balance": new_balance,
                                        "plan_credits": plan_credits,
                                        "subscription_expires_at": expires_at.isoformat(),
                                        "updated_at": datetime.now().isoformat()
                                    }).eq("account_id", canonical_account_id).execute()
                                else:
                                    supabase.table("ai_credit_balances").insert({
                                        "account_id": canonical_account_id,
                                        "balance": plan_credits,
                                        "plan_credits": plan_credits,
                                        "topup_credits": 0,
                                        "subscription_expires_at": expires_at.isoformat(),
                                        "updated_at": datetime.now().isoformat()
                                    }).execute()
                            else:
                                logging.info(f"Subscription already exists for {plan_code}")
                    except Exception as e:
                        logging.error(f"Failed to update subscription: {e}")
        
        return "OK", 200
    except Exception as e:
        logging.error(f"Webhook error: {e}")
        return "Error", 500

@app.route('/api/whatsapp/webhook', methods=['GET', 'POST'])
def webhook():
    if request.method == 'GET':
        mode = request.args.get('hub.mode')
        token = request.args.get('hub.verify_token')
        challenge = request.args.get('hub.challenge')
        if mode == 'subscribe' and token == WHATSAPP_VERIFY_TOKEN:
            return challenge, 200
        return "Verification failed", 403
    
    try:
        body = request.get_json()
        if not body:
            return "ok"
        
        entry = body.get('entry', [{}])[0]
        changes = entry.get('changes', [{}])[0]
        value = changes.get('value', {})
        messages = value.get('messages', [])
        
        for msg in messages:
            from_number = msg.get('from')
            msg_type = msg.get('type')
            
            if msg_type == 'text':
                text = msg.get('text', {}).get('body', '').strip()
                
                current_time = datetime.now().timestamp()
                cooldown_key = f"{from_number}:{text}"
                if user_cooldown[cooldown_key] > current_time - 2:
                    continue
                user_cooldown[cooldown_key] = current_time
                
                if from_number in user_state:
                    state_time = user_state[from_number].get("timestamp", 0)
                    if current_time - state_time > 300:
                        user_state.pop(from_number, None)
                
                logging.info(f"Message from {from_number}: {text}")
                
                if not supabase:
                    send_whatsapp(from_number, "❌ Service unavailable. Please try again later.")
                    continue
                
                canonical_account_id = get_canonical_account_id(from_number)
                if not canonical_account_id:
                    send_whatsapp(from_number, "❌ Error initializing your account. Please try again later.")
                    continue
                
                # ============ GLOBAL COMMANDS ============
                if text == '#':
                    user_state.pop(from_number, None)
                    send_whatsapp(from_number, get_main_menu())
                    continue
                
                if text == '0':
                    user_state.pop(from_number, None)
                    send_whatsapp(from_number, "❌ Cancelled.\n\nReply 8 for main menu.")
                    continue
                
                if text == '*':
                    user_state.pop(from_number, None)
                    send_whatsapp(from_number, get_main_menu())
                    continue
                
                # ============ CALCULATOR COMMAND (Free feature) ============
                if text.upper().startswith('CALC'):
                    result = handle_calc_command(from_number, text, canonical_account_id)
                    send_whatsapp(from_number, result)
                    continue
                
                # ============ DIRECT T-CODE CREDIT PURCHASE ============
                t_code = text.upper().strip()
                if t_code in ["T10", "T50", "T100", "T500"]:
                    if not has_active_subscription(canonical_account_id):
                        send_whatsapp(from_number, f"""❌ *Subscription Required*

Top-up credits can only be purchased with an active subscription.

Reply 4 to view plans.

{DISCLAIMER_MAIN}""")
                        continue
                    
                    package = CREDIT_PACKAGES.get(t_code)
                    if package:
                        payment = create_credit_payment(canonical_account_id, t_code, from_number)
                        if payment and payment.get("success"):
                            send_whatsapp(from_number, f"""💎 *Payment Link Generated!*

Package: {package['description']}
Amount: ₦{package['amount_ngn']:,}

🔗 {payment['payment_link']}

Reference: {payment['reference']}

⚠️ Top-up credits require active subscription.
{DISCLAIMER_CREDITS}
0 - Cancel | # - Main Menu""")
                        else:
                            send_whatsapp(from_number, "❌ Failed to generate payment link.\n\nReply 8 for menu.")
                    else:
                        send_whatsapp(from_number, "❌ Invalid code. Use T10, T50, T100, or T500.")
                    continue
                
                # ============ OPTION 5 - PREMIUM FEATURES INFO ============
                if text == '5':
                    send_whatsapp(from_number, f"""🔗 *Premium Features*

✨ Available with active subscription:

• AI-powered tax answers (1 credit)
• PAYE Filing Assistance (10 credits)
• VAT Return Preparation (15 credits)
• CIT Filing (20 credits)
• Document generation (5-10 credits)

{DISCLAIMER_MAIN}

Reply 4 to view plans or 6 to buy top-ups""")
                    continue
                
                # ============ OPTION 6 - BUY TOP-UP CREDITS ============
                if text == '6':
                    if not has_active_subscription(canonical_account_id):
                        send_whatsapp(from_number, f"""❌ *Subscription Required*

Top-up credits can only be purchased with an active subscription.

Reply 4 to view plans.

{DISCLAIMER_CREDITS}""")
                        continue
                    user_state[from_number] = {"step": "buy_credits", "timestamp": current_time}
                    send_whatsapp(from_number, get_credit_packages_menu())
                    continue
                
                # ============ OPTION 7 - TAX FILING & MANAGEMENT ============
                if text == '7':
                    if not has_active_subscription(canonical_account_id):
                        send_whatsapp(from_number, f"""❌ *Premium Feature*

Tax Filing & Management requires an active subscription.

{DISCLAIMER_FILING}

Reply 4 to view plans""")
                        continue
                    
                    user_state[from_number] = {"step": "filing_menu", "timestamp": current_time}
                    send_whatsapp(from_number, get_filing_menu())
                    continue
                
                # ============ CREDIT PACKAGE SELECTION (after pressing 6) ============
                if from_number in user_state and user_state[from_number].get("step") == "buy_credits":
                    package_code = text.upper().strip()
                    
                    if package_code in ["T10", "T50", "T100", "T500"]:
                        if not has_active_subscription(canonical_account_id):
                            send_whatsapp(from_number, "❌ Subscription required for top-ups. Reply 4 to view plans.")
                            user_state.pop(from_number, None)
                            continue
                        
                        package = CREDIT_PACKAGES.get(package_code)
                        if package:
                            payment = create_credit_payment(canonical_account_id, package_code, from_number)
                            if payment and payment.get("success"):
                                send_whatsapp(from_number, f"""💎 *Payment Link Generated!*

Package: {package['description']}
Amount: ₦{package['amount_ngn']:,}

🔗 {payment['payment_link']}

Reference: {payment['reference']}

{DISCLAIMER_CREDITS}
0 - Cancel | # - Main Menu""")
                                user_state.pop(from_number, None)
                            else:
                                send_whatsapp(from_number, "❌ Failed to generate payment link.\n\nReply 8 for menu.")
                                user_state.pop(from_number, None)
                    elif text == '0':
                        user_state.pop(from_number, None)
                        send_whatsapp(from_number, "❌ Cancelled.\n\nReply 8 for menu.")
                    else:
                        send_whatsapp(from_number, "Please reply with T10, T50, T100, T500, or 0 to cancel.")
                    continue
                
                # ============ SUBSCRIPTION PLAN SELECTION ============
                if from_number in user_state and user_state[from_number].get("step") == 2:
                    plan = user_state[from_number].get("plan")
                    email = text.strip().lower()
                    
                    email_pattern = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')
                    
                    if email_pattern.match(email):
                        reference = f"SUB_{plan.get('plan_code')}_{uuid.uuid4().hex[:8]}"
                        amount = plan.get("price", 0) * 100
                        base_url = os.getenv("PUBLIC_BACKEND_BASE_URL", "https://incredible-nonie-bmsconcept-37359733.koyeb.app")
                        callback_url = f"{base_url}/payment/success?phone={from_number}&plan={plan.get('name')}"
                        
                        payload = {
                            "amount": amount,
                            "email": email,
                            "reference": reference,
                            "currency": "NGN",
                            "metadata": {
                                "plan_code": plan.get("plan_code"),
                                "plan_name": plan.get("name"),
                                "phone": from_number,
                                "channel": "whatsapp",
                                "type": "subscription"
                            },
                            "callback_url": callback_url
                        }
                        
                        headers = {
                            "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
                            "Content-Type": "application/json"
                        }
                        
                        try:
                            response = requests.post(f"{PAYSTACK_API_URL}/transaction/initialize", json=payload, headers=headers, timeout=30)
                            
                            if response.status_code == 200:
                                data = response.json()
                                if data.get("status"):
                                    payment_link = data["data"]["authorization_url"]
                                    send_whatsapp(from_number, f"""✅ *Payment Link Generated!*

Plan: {plan.get('name')}
Amount: ₦{plan.get('price', 0):,}

🔗 {payment_link}

Reference: {reference}

{DISCLAIMER_SUBSCRIPTION}
0 - Cancel | # - Main Menu""")
                                    user_state.pop(from_number, None)
                                else:
                                    send_whatsapp(from_number, "❌ Failed to generate payment link.")
                                    user_state.pop(from_number, None)
                            else:
                                send_whatsapp(from_number, "❌ Payment service error.")
                                user_state.pop(from_number, None)
                        except Exception as e:
                            logging.error(f"Payment error: {e}")
                            send_whatsapp(from_number, "❌ Failed to generate payment link.")
                            user_state.pop(from_number, None)
                    elif text == '0' or text == '#':
                        user_state.pop(from_number, None)
                        send_whatsapp(from_number, "❌ Cancelled.\n\nReply 8 for menu.")
                    else:
                        send_whatsapp(from_number, "❌ Invalid email. Send a valid email address, 0 to cancel, or # for menu.")
                    continue
                
                # ============ FILING MENU HANDLER (F1, F2, F3, F4, F5, F0) ============
                if from_number in user_state and user_state[from_number].get("step") == "filing_menu":
                    filing_code = text.upper().strip()
                    
                    if filing_code == "F1":
                        user_state[from_number] = {
                            "step": "paye_filing",
                            "filing_step": 1,
                            "inputs": {},
                            "timestamp": current_time
                        }
                        send_whatsapp(from_number, get_paye_filing_questions(1))
                    elif filing_code == "F2":
                        user_state[from_number] = {
                            "step": "vat_filing",
                            "filing_step": 1,
                            "inputs": {},
                            "timestamp": current_time
                        }
                        send_whatsapp(from_number, get_vat_filing_questions(1))
                    elif filing_code == "F3":
                        user_state[from_number] = {
                            "step": "cit_filing",
                            "filing_step": 1,
                            "inputs": {},
                            "timestamp": current_time
                        }
                        send_whatsapp(from_number, get_cit_filing_questions(1))
                    elif filing_code == "F4":
                        user_state[from_number] = {"step": "doc_menu", "timestamp": current_time}
                        send_whatsapp(from_number, get_document_generation_menu())
                    elif filing_code == "F5":
                        history = get_filing_history(canonical_account_id)
                        send_whatsapp(from_number, history)
                        user_state.pop(from_number, None)
                    elif filing_code == "F0":
                        user_state.pop(from_number, None)
                        send_whatsapp(from_number, get_main_menu())
                    elif text == '0':
                        user_state.pop(from_number, None)
                        send_whatsapp(from_number, "❌ Cancelled.\n\nReply 8 for main menu.")
                    else:
                        send_whatsapp(from_number, "Please reply with F1, F2, F3, F4, F5, F0, or 0 to cancel.")
                    continue
                
                # ============ DOCUMENT GENERATION MENU (F4-1 to F4-5) ============
                if from_number in user_state and user_state[from_number].get("step") == "doc_menu":
                    doc_cmd = text.upper().strip()
                    
                    if doc_cmd.startswith("F4-"):
                        doc_num = doc_cmd.replace("F4-", "")
                        if doc_num in ['1', '2', '3', '4', '5']:
                            doc_info = {
                                '1': {'name': 'Tax Payment Receipt', 'cost': 5},
                                '2': {'name': 'PAYE Filing Form', 'cost': 5},
                                '3': {'name': 'VAT Return Form', 'cost': 5},
                                '4': {'name': 'CIT Computation Report', 'cost': 10},
                                '5': {'name': 'Annual Tax Summary', 'cost': 10}
                            }
                            doc = doc_info[doc_num]
                            
                            credit_details = get_credit_details(canonical_account_id)
                            if int(credit_details.get("balance", 0)) < doc['cost']:
                                send_whatsapp(from_number, f"""❌ *Insufficient Credits*

Need {doc['cost']} credits to generate {doc['name']}.
Current balance: {credit_details.get('balance', 0)} credits

Buy top-ups: T10, T50, T100, T500

{DISCLAIMER_CREDITS}""")
                                user_state.pop(from_number, None)
                                continue
                            
                            success, message = deduct_credits(canonical_account_id, doc['cost'], f"Document: {doc['name']}")
                            if not success:
                                send_whatsapp(from_number, f"❌ {message}")
                                user_state.pop(from_number, None)
                                continue
                            
                            doc_ref, result = process_document_generation(doc_num, canonical_account_id, {})
                            save_filing_record(canonical_account_id, "DOCUMENT", doc_ref, {"document_type": doc['name']}, result, doc['cost'])
                            send_whatsapp(from_number, result)
                            user_state.pop(from_number, None)
                        else:
                            send_whatsapp(from_number, "Invalid document type. Use F4-1 to F4-5.")
                    elif doc_cmd == "F0":
                        user_state[from_number] = {"step": "filing_menu", "timestamp": current_time}
                        send_whatsapp(from_number, get_filing_menu())
                    elif text == '0':
                        user_state.pop(from_number, None)
                        send_whatsapp(from_number, "❌ Cancelled.\n\nReply 8 for main menu.")
                    else:
                        send_whatsapp(from_number, "Please use F4-1 to F4-5, F0 for back, or 0 to cancel.")
                    continue
                
                # ============ PAYE FILING STEPS ============
                if from_number in user_state and user_state[from_number].get("step") == "paye_filing":
                    state = user_state[from_number]
                    step = state.get("filing_step", 1)
                    inputs = state.get("inputs", {})
                    
                    if text == '0':
                        user_state.pop(from_number, None)
                        send_whatsapp(from_number, "❌ Filing cancelled.\n\nReply 8 for main menu.")
                        continue
                    
                    if step == 1:
                        try:
                            inputs["salary"] = float(text.replace(',', ''))
                            user_state[from_number] = {"step": "paye_filing", "filing_step": 2, "inputs": inputs, "timestamp": current_time}
                            send_whatsapp(from_number, get_paye_filing_questions(2))
                        except:
                            send_whatsapp(from_number, "❌ Invalid amount. Please enter a valid number.")
                    elif step == 2:
                        try:
                            inputs["pension"] = float(text.replace(',', ''))
                            user_state[from_number] = {"step": "paye_filing", "filing_step": 3, "inputs": inputs, "timestamp": current_time}
                            send_whatsapp(from_number, get_paye_filing_questions(3))
                        except:
                            send_whatsapp(from_number, "❌ Invalid amount. Please enter a valid number.")
                    elif step == 3:
                        try:
                            inputs["nhf"] = float(text.replace(',', ''))
                            user_state[from_number] = {"step": "paye_filing", "filing_step": 4, "inputs": inputs, "timestamp": current_time}
                            send_whatsapp(from_number, get_paye_filing_questions(4))
                        except:
                            send_whatsapp(from_number, "❌ Invalid amount. Please enter a valid number.")
                    elif step == 4:
                        try:
                            inputs["allowances"] = float(text.replace(',', ''))
                            user_state[from_number] = {"step": "paye_filing", "filing_step": 5, "inputs": inputs, "timestamp": current_time}
                            send_whatsapp(from_number, get_paye_filing_questions(5))
                        except:
                            send_whatsapp(from_number, "❌ Invalid amount. Please enter a valid number.")
                    elif step == 5:
                        try:
                            inputs["reliefs"] = float(text.replace(',', '')) if text != '0' else 200000
                            
                            cost = TAX_FILING_COSTS["paye_assistance"]
                            credit_details = get_credit_details(canonical_account_id)
                            if int(credit_details.get("balance", 0)) < cost:
                                send_whatsapp(from_number, f"""❌ *Insufficient Credits*

Need {cost} credits for PAYE filing.
Current balance: {credit_details.get('balance', 0)} credits

Buy top-ups: T10, T50, T100, T500

{DISCLAIMER_CREDITS}""")
                                user_state.pop(from_number, None)
                                continue
                            
                            success, message = deduct_credits(canonical_account_id, cost, "PAYE Filing Assistance")
                            if not success:
                                send_whatsapp(from_number, f"❌ {message}")
                                user_state.pop(from_number, None)
                                continue
                            
                            result = process_paye_filing(inputs)
                            reference = f"PAYE_{datetime.now().strftime('%Y%m%d')}_{uuid.uuid4().hex[:6]}"
                            save_filing_record(canonical_account_id, "PAYE", reference, inputs, result, cost)
                            send_whatsapp(from_number, result)
                            user_state.pop(from_number, None)
                        except Exception as e:
                            logging.error(f"PAYE filing error: {e}")
                            send_whatsapp(from_number, f"❌ Error processing filing: {str(e)}")
                            user_state.pop(from_number, None)
                    continue
                
                # ============ VAT FILING STEPS ============
                if from_number in user_state and user_state[from_number].get("step") == "vat_filing":
                    state = user_state[from_number]
                    step = state.get("filing_step", 1)
                    inputs = state.get("inputs", {})
                    
                    if text == '0':
                        user_state.pop(from_number, None)
                        send_whatsapp(from_number, "❌ Filing cancelled.\n\nReply 8 for main menu.")
                        continue
                    
                    if step == 1:
                        try:
                            inputs["sales"] = float(text.replace(',', ''))
                            user_state[from_number] = {"step": "vat_filing", "filing_step": 2, "inputs": inputs, "timestamp": current_time}
                            send_whatsapp(from_number, get_vat_filing_questions(2))
                        except:
                            send_whatsapp(from_number, "❌ Invalid amount. Please enter a valid number.")
                    elif step == 2:
                        try:
                            inputs["purchases"] = float(text.replace(',', ''))
                            user_state[from_number] = {"step": "vat_filing", "filing_step": 3, "inputs": inputs, "timestamp": current_time}
                            send_whatsapp(from_number, get_vat_filing_questions(3))
                        except:
                            send_whatsapp(from_number, "❌ Invalid amount. Please enter a valid number.")
                    elif step == 3:
                        if text == '1':
                            inputs["vat_rate"] = 7.5
                        else:
                            try:
                                inputs["vat_rate"] = float(text.replace('%', ''))
                            except:
                                send_whatsapp(from_number, "❌ Invalid rate. Enter 1 for 7.5%, or a custom rate (e.g., 10)")
                                continue
                        user_state[from_number] = {"step": "vat_filing", "filing_step": 4, "inputs": inputs, "timestamp": current_time}
                        send_whatsapp(from_number, get_vat_filing_questions(4))
                    elif step == 4:
                        inputs["period"] = text.strip()
                        
                        cost = TAX_FILING_COSTS["vat_preparation"]
                        credit_details = get_credit_details(canonical_account_id)
                        if int(credit_details.get("balance", 0)) < cost:
                            send_whatsapp(from_number, f"""❌ *Insufficient Credits*

Need {cost} credits for VAT filing.
Current balance: {credit_details.get('balance', 0)} credits

Buy top-ups: T10, T50, T100, T500

{DISCLAIMER_CREDITS}""")
                            user_state.pop(from_number, None)
                            continue
                        
                        success, message = deduct_credits(canonical_account_id, cost, "VAT Return Preparation")
                        if not success:
                            send_whatsapp(from_number, f"❌ {message}")
                            user_state.pop(from_number, None)
                            continue
                        
                        result = process_vat_filing(inputs)
                        reference = f"VAT_{datetime.now().strftime('%Y%m%d')}_{uuid.uuid4().hex[:6]}"
                        save_filing_record(canonical_account_id, "VAT", reference, inputs, result, cost)
                        send_whatsapp(from_number, result)
                        user_state.pop(from_number, None)
                    continue
                
                # ============ CIT FILING STEPS ============
                if from_number in user_state and user_state[from_number].get("step") == "cit_filing":
                    state = user_state[from_number]
                    step = state.get("filing_step", 1)
                    inputs = state.get("inputs", {})
                    
                    if text == '0':
                        user_state.pop(from_number, None)
                        send_whatsapp(from_number, "❌ Filing cancelled.\n\nReply 8 for main menu.")
                        continue
                    
                    if step == 1:
                        try:
                            inputs["revenue"] = float(text.replace(',', ''))
                            user_state[from_number] = {"step": "cit_filing", "filing_step": 2, "inputs": inputs, "timestamp": current_time}
                            send_whatsapp(from_number, get_cit_filing_questions(2))
                        except:
                            send_whatsapp(from_number, "❌ Invalid amount. Please enter a valid number.")
                    elif step == 2:
                        try:
                            inputs["cost_of_sales"] = float(text.replace(',', ''))
                            user_state[from_number] = {"step": "cit_filing", "filing_step": 3, "inputs": inputs, "timestamp": current_time}
                            send_whatsapp(from_number, get_cit_filing_questions(3))
                        except:
                            send_whatsapp(from_number, "❌ Invalid amount. Please enter a valid number.")
                    elif step == 3:
                        try:
                            inputs["expenses"] = float(text.replace(',', ''))
                            user_state[from_number] = {"step": "cit_filing", "filing_step": 4, "inputs": inputs, "timestamp": current_time}
                            send_whatsapp(from_number, get_cit_filing_questions(4))
                        except:
                            send_whatsapp(from_number, "❌ Invalid amount. Please enter a valid number.")
                    elif step == 4:
                        try:
                            inputs["allowances"] = float(text.replace(',', ''))
                            user_state[from_number] = {"step": "cit_filing", "filing_step": 5, "inputs": inputs, "timestamp": current_time}
                            send_whatsapp(from_number, get_cit_filing_questions(5))
                        except:
                            send_whatsapp(from_number, "❌ Invalid amount. Please enter a valid number.")
                    elif step == 5:
                        inputs["tax_year"] = text.strip()
                        
                        cost = TAX_FILING_COSTS["cit_filing"]
                        credit_details = get_credit_details(canonical_account_id)
                        if int(credit_details.get("balance", 0)) < cost:
                            send_whatsapp(from_number, f"""❌ *Insufficient Credits*

Need {cost} credits for CIT filing.
Current balance: {credit_details.get('balance', 0)} credits

Buy top-ups: T10, T50, T100, T500

{DISCLAIMER_CREDITS}""")
                            user_state.pop(from_number, None)
                            continue
                        
                        success, message = deduct_credits(canonical_account_id, cost, "CIT Filing")
                        if not success:
                            send_whatsapp(from_number, f"❌ {message}")
                            user_state.pop(from_number, None)
                            continue
                        
                        result = process_cit_filing(inputs)
                        reference = f"CIT_{inputs['tax_year']}_{uuid.uuid4().hex[:6]}"
                        save_filing_record(canonical_account_id, "CIT", reference, inputs, result, cost)
                        send_whatsapp(from_number, result)
                        user_state.pop(from_number, None)
                    continue
                
                # ============ TAX QUESTIONS ============
                
                has_sub = has_active_subscription(canonical_account_id)
                
                # Handle tax question (when in asking state after pressing 1)
                if from_number in user_state and user_state[from_number].get("step") == "asking_question":
                    db_response, found = handle_database_answer(canonical_account_id, text)
                    
                    if found and db_response:
                        send_whatsapp(from_number, f"{db_response}\n\n---\n📚 *Answer from database*\n\n{DISCLAIMER_AI}\n\nReply 1 for another question or 8 for menu.")
                        user_state.pop(from_number, None)
                        continue
                    
                    if not has_sub:
                        send_whatsapp(from_number, f"""❌ *Premium Feature*

This question requires AI assistance, which is a premium feature.

📋 *To access AI answers:*
1. Subscribe to a plan (Reply 4)
2. Get monthly credits
3. Each AI answer = 1 credit

💡 Free tier includes database answers (50/day) and tax calculations (CALC command).

{DISCLAIMER_AI}

Reply 4 to view plans""")
                        user_state.pop(from_number, None)
                        continue
                    
                    ai_response, success = handle_ai_question(canonical_account_id, text)
                    send_whatsapp(from_number, ai_response)
                    user_state.pop(from_number, None)
                    continue
                
                # Handle direct question (no state, just type a question)
                is_question = (len(text) > 10 and not text.upper().startswith('T') and not text.upper().startswith('F') and not text.upper().startswith('CALC') and not text.isdigit() and text not in ['#', '*', '0', '1', '2', '3', '4', '5', '6', '7', '8', '9'])
                
                if is_question and from_number not in user_state:
                    db_response, found = handle_database_answer(canonical_account_id, text)
                    
                    if found and db_response:
                        send_whatsapp(from_number, f"{db_response}\n\n---\n📚 *Answer from database*\n\n{DISCLAIMER_AI}\n\nReply 1 for another question or 8 for menu.")
                        continue
                    
                    if not has_sub:
                        send_whatsapp(from_number, f"""❌ *Premium Feature*

This question requires AI assistance, which is a premium feature.

📋 *To access AI answers:*
1. Subscribe to a plan (Reply 4)
2. Get monthly credits
3. Each AI answer = 1 credit

💡 Free tier includes database answers (50/day) and tax calculations (CALC command).

{DISCLAIMER_AI}

Reply 4 to view plans""")
                        continue
                    
                    ai_response, success = handle_ai_question(canonical_account_id, text)
                    send_whatsapp(from_number, ai_response)
                    continue
                
                # ============ MAIN MENU NAVIGATION ============
                if text == '4':
                    send_whatsapp(from_number, get_plans_list_menu())
                    user_state[from_number] = {"step": "selecting_plan", "timestamp": current_time}
                elif text == '8':
                    send_whatsapp(from_number, get_main_menu())
                elif text == '3':
                    subscription = get_user_subscription(from_number)
                    plan = None
                    if subscription:
                        plan_code = subscription.get("plan_code")
                        plans = get_all_plans()
                        for p in plans:
                            if p.get("plan_code") == plan_code:
                                plan = p
                                break
                    credit_details = get_credit_details(canonical_account_id)
                    send_whatsapp(from_number, format_subscription_message(subscription, plan, credit_details))
                elif text == '1':
                    user_state[from_number] = {"step": "asking_question", "timestamp": current_time}
                    send_whatsapp(from_number, "💬 Please type your tax question.\n\n💡 I'll check my database first (free). If not found, AI will answer (1 credit for subscribers).\n\n# - Menu | 0 - Cancel")
                elif text == '2':
                    credit_details = get_credit_details(canonical_account_id)
                    total_balance = int(credit_details.get("balance", 0))
                    topup_credits = int(credit_details.get("topup_credits", 0))
                    plan_credits = int(credit_details.get("plan_credits", 0))
                    
                    if has_sub:
                        send_whatsapp(from_number, f"""💎 *Credit Balance*

✅ ACTIVE SUBSCRIPTION
📊 Total available: *{total_balance}* credits
• Top-up credits: {topup_credits} (used first)
• Plan credits: {plan_credits}

💡 *Credit Usage:*
• AI question: 1 credit
• PAYE filing: 10 credits
• VAT filing: 15 credits
• CIT filing: 20 credits
• Document generation: 5-10 credits

{DISCLAIMER_CREDITS}

To buy top-ups: T10, T50, T100, T500""")
                    else:
                        db_allowed, db_limit = check_daily_limit(canonical_account_id, "db_answers")
                        calc_allowed, calc_limit = check_daily_limit(canonical_account_id, "calculations")
                        
                        send_whatsapp(from_number, f"""💎 *Free Plan*

📊 *Daily Limits:*
• Database answers: {db_limit if db_allowed else 'Limit reached'}
• Tax calculations: {calc_limit if calc_allowed else 'Limit reached'} (use CALC command)
• AI answers: 0 (requires subscription)

💡 *Upgrade to subscribe:*
• Monthly credits
• AI answers (1 credit)
• Document features
• Tax filing assistance

{DISCLAIMER_MAIN}

Reply 4 to view plans""")
                
                # ============ NO AUTOMATIC CALCULATOR ON NUMBERS ============
                # Calculator is now only via CALC command, not on random numbers
                
                else:
                    if from_number in user_state and user_state[from_number].get("step") == "selecting_plan":
                        plans = get_all_plans()
                        result = find_plan_by_input(plans, text)
                        
                        if result.get("found") and not result.get("ambiguous"):
                            plan = result.get("plan")
                            user_state[from_number] = {"step": 2, "plan": plan, "timestamp": datetime.now().timestamp()}
                            send_whatsapp(from_number, f"""✅ *Plan Selected:* {plan.get('name')}

💰 Price: ₦{plan.get('price', 0):,}
🎯 Credits: {plan.get('ai_credits_total', 0)} credits/month

✨ *Premium features included:*
• AI answers (1 credit each)
• PAYE filing (10 credits)
• VAT filing (15 credits)
• CIT filing (20 credits)
• Document generation (5-10 credits)

{DISCLAIMER_SUBSCRIPTION}

📧 *Please provide your email address* for payment link.

Email example: name@example.com

0 - Cancel | # - Menu""")
                        else:
                            send_whatsapp(from_number, get_main_menu())
                    else:
                        send_whatsapp(from_number, get_main_menu())
        
        return "ok"
    except Exception as e:
        logging.error(f"Error in webhook: {e}")
        return "error", 500

def find_plan_by_input(plans, user_input):
    user_input = user_input.strip()
    
    code_map = {
        "S1": "starter_monthly",
        "S2": "starter_quarterly", 
        "S3": "starter_yearly",
        "P1": "professional_monthly",
        "P2": "professional_quarterly",
        "P3": "professional_yearly",
        "B1": "business_monthly",
        "B2": "business_quarterly",
        "B3": "business_yearly"
    }
    
    target_code = code_map.get(user_input.upper())
    if target_code:
        for plan in plans:
            if plan.get("plan_code", "") == target_code:
                return {"found": True, "plan": plan, "ambiguous": False}
    
    for plan in plans:
        if plan.get("name", "").lower() == user_input.lower():
            return {"found": True, "plan": plan, "ambiguous": False}
    
    try:
        num = int(re.sub(r'[^\d]', '', user_input))
        for plan in plans:
            if plan.get("ai_credits_total", 0) == num:
                return {"found": True, "plan": plan, "ambiguous": False}
    except:
        pass
    
    return {"found": False}

def create_credit_payment(account_id, package_code, phone_number):
    existing_reference = check_pending_transaction(account_id)
    if existing_reference and supabase:
        supabase.table("paystack_transactions").update({
            "status": "expired",
            "updated_at": datetime.now().isoformat()
        }).eq("reference", existing_reference).execute()
    
    package = CREDIT_PACKAGES.get(package_code.upper())
    if not package:
        return None
    
    reference = f"CREDIT_{package['credits']}_{uuid.uuid4().hex[:8]}"
    amount_kobo = package["amount_kobo"]
    credits = package["credits"]
    
    base_url = os.getenv("PUBLIC_BACKEND_BASE_URL", "https://incredible-nonie-bmsconcept-37359733.koyeb.app")
    callback_url = f"{base_url}/payment/success?phone={phone_number}&type=credits&credits={credits}"
    
    payload = {
        "amount": amount_kobo,
        "email": f"wa_{phone_number}@temp.ng",
        "reference": reference,
        "currency": "NGN",
        "metadata": {
            "account_id": account_id,
            "credits": credits,
            "package_code": package_code,
            "type": "credit_purchase",
            "channel_type": "whatsapp",
            "provider_user_id": phone_number,
            "amount_ngn": package["amount_ngn"]
        },
        "callback_url": callback_url
    }
    
    headers = {
        "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.post(f"{PAYSTACK_API_URL}/transaction/initialize", json=payload, headers=headers, timeout=30)
        
        if response.status_code == 200:
            data = response.json()
            if data.get("status") and supabase:
                supabase.table("paystack_transactions").insert({
                    "reference": reference,
                    "account_id": account_id,
                    "amount": amount_kobo,
                    "currency": "NGN",
                    "status": "pending",
                    "plan_code": package_code,
                    "metadata": payload["metadata"],
                    "created_at": datetime.now().isoformat(),
                    "updated_at": datetime.now().isoformat()
                }).execute()
                
                return {
                    "success": True,
                    "payment_link": data["data"]["authorization_url"],
                    "reference": reference,
                    "credits": credits,
                    "amount": package["amount_ngn"]
                }
        
        logging.error(f"Paystack error: {response.text}")
        return None
    except Exception as e:
        logging.error(f"Payment error: {e}")
        return None

def check_pending_transaction(account_id):
    try:
        if not supabase:
            return None
        five_min_ago = (datetime.now() - timedelta(minutes=5)).isoformat()
        pending = supabase.table("paystack_transactions")\
            .select("reference, metadata")\
            .eq("account_id", account_id)\
            .eq("status", "pending")\
            .gte("created_at", five_min_ago)\
            .execute()
        
        if pending.data:
            for tx in pending.data:
                metadata = tx.get("metadata", {})
                if metadata.get("type") == "credit_purchase":
                    return tx.get("reference")
        return None
    except Exception as e:
        logging.warning(f"Error checking pending transaction: {e}")
        return None

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8000))
    app.run(host='0.0.0.0', port=port)