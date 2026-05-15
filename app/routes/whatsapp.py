# app/routes/whatsapp.py
"""WhatsApp bot routes and handlers - Converted from ws.py"""

from flask import Blueprint, request, jsonify
from datetime import datetime, timedelta
import logging
import uuid
import re
import os
from collections import defaultdict

# Import shared services
from app.services.ask_service import ask_guarded
from app.core.supabase_client import supabase_client as supabase_client as supabase
import requests

bp = Blueprint("whatsapp", __name__)

# ============ LEGAL DISCLAIMERS ============
DISCLAIMER_MAIN = "?? *AI may make mistakes. Always verify with official sources.*"
DISCLAIMER_AI = "?? *AI-generated. Verify important information.*"
DISCLAIMER_CALC = "?? *Estimate only. Actual tax may vary.*"
DISCLAIMER_FILING = "?? *Record saved. Not an official filing with tax authorities.*"
DISCLAIMER_DOC = "?? *For reference only. Not legally binding.*"
DISCLAIMER_CREDITS = "? *Transaction recorded. Contact support for issues.*"
DISCLAIMER_SUBSCRIPTION = "? *Subscription active. Auto-renews unless cancelled.*"

# ============ WHATSAPP CONFIGURATION ============
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "naija-tax-guide-verify")
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
WHATSAPP_API_URL = "https://graph.facebook.com/v18.0"

# Credit packages
CREDIT_PACKAGES = {
    "T10": {"credits": 10, "amount_ngn": 500, "amount_kobo": 50000, "code": "T10", "description": "10 AI Credits", "requires_subscription": True},
    "T50": {"credits": 50, "amount_ngn": 2000, "amount_kobo": 200000, "code": "T50", "description": "50 AI Credits", "requires_subscription": True},
    "T100": {"credits": 100, "amount_ngn": 3500, "amount_kobo": 350000, "code": "T100", "description": "100 AI Credits", "requires_subscription": True},
    "T500": {"credits": 500, "amount_ngn": 15000, "amount_kobo": 1500000, "code": "T500", "description": "500 AI Credits", "requires_subscription": True},
}

TAX_FILING_COSTS = {
    "paye_assistance": 10,
    "vat_preparation": 15,
    "cit_filing": 20,
    "document_generation_simple": 5,
    "document_generation_complex": 10,
    "filing_summary": 5
}

FREE_PLAN_LIMITS = {
    "db_answers_daily": 50,
    "calculations_daily": 20
}

# In-memory cache
user_state = {}
user_cooldown = defaultdict(float)
daily_usage_cache = {}

# Paystack API URL
PAYSTACK_API_URL = "https://api.paystack.co"
# ============ FILING SESSION MANAGEMENT ============

def create_filing_session(account_id, phone_number, filing_type):
    """Create a new filing session in database"""
    try:
        supabase.table("filing_sessions")\
            .update({"status": "cancelled", "updated_at": datetime.now().isoformat()})\
            .eq("account_id", account_id)\
            .eq("status", "active")\
            .execute()
        
        supabase.table("filing_sessions").insert({
            "account_id": account_id,
            "phone_number": phone_number,
            "filing_type": filing_type,
            "current_step": 1,
            "inputs": {},
            "status": "active",
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat()
        }).execute()
        
        logging.info(f"? Created filing session for {phone_number}: {filing_type}")
        return True
    except Exception as e:
        logging.error(f"Error creating filing session: {e}")
        return False

def get_active_filing_session(account_id):
    """Get active filing session from database"""
    try:
        result = supabase.table("filing_sessions")\
            .select("*")\
            .eq("account_id", account_id)\
            .eq("status", "active")\
            .limit(1)\
            .execute()
        
        if result.data:
            return result.data[0]
        return None
    except Exception as e:
        logging.error(f"Error getting filing session: {e}")
        return None

def update_filing_session(account_id, step, inputs):
    """Update existing filing session"""
    try:
        supabase.table("filing_sessions")\
            .update({
                "current_step": step,
                "inputs": inputs,
                "updated_at": datetime.now().isoformat()
            })\
            .eq("account_id", account_id)\
            .eq("status", "active")\
            .execute()
        return True
    except Exception as e:
        logging.error(f"Error updating filing session: {e}")
        return False

def cancel_filing_session(account_id):
    """Cancel active filing session"""
    try:
        supabase.table("filing_sessions")\
            .update({"status": "cancelled", "updated_at": datetime.now().isoformat()})\
            .eq("account_id", account_id)\
            .eq("status", "active")\
            .execute()
        return True
    except Exception as e:
        logging.error(f"Error cancelling filing session: {e}")
        return False

# ============ ACCOUNT MANAGEMENT ============

def get_canonical_account_id(phone_number):
    """Get or create canonical account_id"""
    if not supabase:
        return None
    
    try:
        account_result = supabase.table("accounts").select("account_id").eq("provider_user_id", str(phone_number)).execute()
        if account_result.data:
            return account_result.data[0].get("account_id")
        
        user_result = supabase.table("bot_users").select("auth_user_id").eq("platform", "whatsapp").eq("user_id", str(phone_number)).execute()
        if user_result.data and user_result.data[0].get("auth_user_id"):
            auth_user_id = user_result.data[0].get("auth_user_id")
            existing = supabase.table("accounts").select("account_id").eq("account_id", auth_user_id).execute()
            if existing.data:
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
        
        return auth_user_id
        
    except Exception as e:
        logging.error(f"Error getting canonical account: {e}")
        return None

def has_active_subscription(account_id):
    """Check if user has active subscription"""
    try:
        result = supabase.table("subscriptions").select("*").eq("account_id", account_id).eq("status", "active").execute()
        if result.data:
            sub = result.data[0]
            expires_at = sub.get("expires_at")
            if expires_at:
                expires_at_clean = expires_at.replace('+00:00', '').replace('Z', '')
                expiry = datetime.fromisoformat(expires_at_clean)
                if expiry < datetime.now():
                    return False
            return True
        return False
    except Exception as e:
        logging.error(f"Error checking subscription: {e}")
        return False

def get_credit_balance(account_id):
    """Get total credit balance"""
    try:
        result = supabase.table("ai_credit_balances").select("balance").eq("account_id", account_id).limit(1).execute()
        if result.data:
            return int(result.data[0].get("balance", 0))
        return 0
    except Exception as e:
        logging.error(f"Error getting balance: {e}")
        return 0

def get_credit_details(account_id):
    """Get detailed credit information"""
    try:
        result = supabase.table("ai_credit_balances").select("*").eq("account_id", account_id).limit(1).execute()
        if result.data:
            return result.data[0]
        return {"balance": 0, "plan_credits": 0, "topup_credits": 0}
    except Exception as e:
        logging.error(f"Error getting credit details: {e}")
        return {"balance": 0, "plan_credits": 0, "topup_credits": 0}

def deduct_credits(account_id, cost, feature_name):
    """Deduct credits from user's balance"""
    try:
        credit_details = get_credit_details(account_id)
        current_topup = int(credit_details.get("topup_credits", 0))
        current_plan = int(credit_details.get("plan_credits", 0))
        current_balance = int(credit_details.get("balance", 0))
        
        if current_balance < cost:
            return False, f"Insufficient credits. Need {cost}, have {current_balance}"
        
        if current_topup >= cost:
            new_topup = current_topup - cost
            new_balance = current_balance - cost
            supabase.table("ai_credit_balances").update({
                "balance": new_balance,
                "topup_credits": new_topup,
                "updated_at": datetime.now().isoformat()
            }).eq("account_id", account_id).execute()
        else:
            remaining = cost - current_topup
            new_balance = current_balance - cost
            new_plan = current_plan - remaining
            supabase.table("ai_credit_balances").update({
                "balance": new_balance,
                "topup_credits": 0,
                "plan_credits": new_plan,
                "updated_at": datetime.now().isoformat()
            }).eq("account_id", account_id).execute()
        
        return True, f"Used {cost} credits for {feature_name}"
    except Exception as e:
        logging.error(f"Error deducting credits: {e}")
        return False, str(e)

def add_topup_credits(account_id, credits, reference):
    """Add top-up credits"""
    if not has_active_subscription(account_id):
        return False, "Active subscription required for top-ups"
    
    try:
        existing = supabase.table("ai_credit_balances").select("*").eq("account_id", account_id).execute()
        
        if existing.data:
            current_balance = int(existing.data[0].get("balance", 0))
            current_topup = int(existing.data[0].get("topup_credits", 0))
            new_topup = current_topup + credits
            new_balance = current_balance + credits
            supabase.table("ai_credit_balances").update({
                "balance": new_balance,
                "topup_credits": new_topup,
                "updated_at": datetime.now().isoformat()
            }).eq("account_id", account_id).execute()
        else:
            supabase.table("ai_credit_balances").insert({
                "account_id": account_id,
                "balance": credits,
                "plan_credits": 0,
                "topup_credits": credits,
                "updated_at": datetime.now().isoformat()
            }).execute()
        
        return True, f"Added {credits} top-up credits"
    except Exception as e:
        return False, str(e)

# ============ TAX CALCULATION FUNCTIONS ============

def calculate_paye(monthly_gross, pension_pct=8, nhf_pct=2.5, allowances=0, relief=200000):
    """Calculate PAYE tax"""
    annual_gross = monthly_gross * 12
    annual_pension = monthly_gross * (pension_pct / 100) * 12
    annual_nhf = monthly_gross * (nhf_pct / 100) * 12
    annual_allowances = allowances * 12
    
    cra_fixed = relief
    cra_one_percent = annual_gross * 0.01
    cra_base = max(cra_fixed, cra_one_percent)
    cra_percentage = annual_gross * 0.20
    cra_total = cra_base + cra_percentage
    
    total_deductions = annual_pension + annual_nhf + annual_allowances + cra_total
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
    net = monthly_gross - (monthly_gross * pension_pct / 100) - (monthly_gross * nhf_pct / 100) - allowances - monthly_tax
    
    return {
        "gross": monthly_gross,
        "pension": round(monthly_gross * pension_pct / 100),
        "nhf": round(monthly_gross * nhf_pct / 100),
        "tax": round(monthly_tax),
        "net": round(net),
        "rate": round((monthly_tax / monthly_gross) * 100, 1)
    }

def calculate_vat(amount, rate=7.5):
    vat = amount * rate / 100
    return {"amount": amount, "rate": rate, "vat": round(vat, 2), "total": round(amount + vat, 2)}

def calculate_cit(revenue, expenses):
    profit = revenue - expenses
    if revenue > 100000000:
        rate = 30
    elif revenue > 25000000:
        rate = 20
    else:
        rate = 0
    cit = profit * rate / 100
    return {"revenue": revenue, "expenses": expenses, "profit": profit, "rate": rate, "cit": round(cit, 2)}

# ============ MESSAGE HANDLERS ============

def get_filing_menu():
    return f"""?? *TAX FILING & MANAGEMENT*

?? *Premium Feature* (Requires Active Subscription)

Reply with:

F1 - PAYE Filing Assistance (10 credits)
F2 - VAT Return Preparation (15 credits)
F3 - CIT Calculation & Filing (20 credits)
F4 - Generate Document (5-10 credits)
F5 - View Filing History
F0 - Back to Main Menu

{DISCLAIMER_FILING}

0 - Cancel | # - Main Menu"""

def get_credit_packages_menu():
    return f"""?? *Buy AI Credits*

?? *Requires Active Subscription*

T10 - 10 credits - ?500
T50 - 50 credits - ?2,000
T100 - 100 credits - ?3,500
T500 - 500 credits - ?15,000

{DISCLAIMER_CREDITS}

0 - Cancel | # - Main Menu"""

def get_plans_list_menu():
    return """?? *AVAILABLE SUBSCRIPTION PLANS*

*STARTER PLANS*
S1 - Starter Monthly - ?5,000/month - 100 credits
S2 - Starter Quarterly - ?14,000/quarter - 300 credits
S3 - Starter Yearly - ?51,000/year - 1,200 credits

*PROFESSIONAL PLANS*
P1 - Professional Monthly - ?12,000/month - 300 credits
P2 - Professional Quarterly - ?33,600/quarter - 900 credits
P3 - Professional Yearly - ?122,400/year - 3,600 credits

*BUSINESS PLANS*
B1 - Business Monthly - ?25,000/month - 800 credits
B2 - Business Quarterly - ?70,000/quarter - 2,400 credits
B3 - Business Yearly - ?255,000/year - 9,600 credits

Reply with plan code (S1, P1, B1, etc.) to subscribe

0 - Cancel | # - Main Menu"""

def get_main_menu():
    return f"""*?? Naija Tax Guide*

1?? - Ask a tax question
2?? - Check credits balance
3?? - Check my subscription
4?? - View subscription plans
5?? - Premium features
6?? - Buy top-up credits
7?? - Tax filing & management
8?? - Help / Menu

*Free Features:*
• CALC 500000 - Calculate PAYE tax
• Database answers (50/day)

*Premium (requires subscription):*
• AI answers (1 credit)
• Tax filing (10-20 credits)

*Commands:* T10, T50, T100, T500 - Buy top-up
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
            logging.error(f"Failed to send: {response.status_code}")
        return False
    except Exception as e:
        logging.error(f"Send error: {e}")
        return False

def process_paye_step(account_id, phone_number, session, text):
    """Process PAYE filing step by step"""
    step = session.get("current_step", 1)
    inputs = session.get("inputs", {})
    
    if step == 1:
        try:
            salary = float(text.replace(',', ''))
            inputs["salary"] = salary
            update_filing_session(account_id, 2, inputs)
            return "?? *PAYE Filing - Step 2/5*\n\nEnter pension contribution (employee):\n(Example: 40000 or 0 if none)\n\n0 - Cancel | # - Menu"
        except:
            return "? Invalid amount. Please enter a valid salary (e.g., 500000)"
    
    elif step == 2:
        try:
            pension = float(text.replace(',', ''))
            inputs["pension"] = pension
            update_filing_session(account_id, 3, inputs)
            return "?? *PAYE Filing - Step 3/5*\n\nEnter NHF contribution (employee):\n(Example: 12500 or 0 if none)\n\n0 - Cancel | # - Menu"
        except:
            return "? Invalid amount. Please enter a valid number"
    
    elif step == 3:
        try:
            nhf = float(text.replace(',', ''))
            inputs["nhf"] = nhf
            update_filing_session(account_id, 4, inputs)
            return "?? *PAYE Filing - Step 4/5*\n\nEnter other allowances (if any):\n(Example: 50000 or 0)\n\n0 - Cancel | # - Menu"
        except:
            return "? Invalid amount. Please enter a valid number"
    
    elif step == 4:
        try:
            allowances = float(text.replace(',', ''))
            inputs["allowances"] = allowances
            update_filing_session(account_id, 5, inputs)
            return "?? *PAYE Filing - Step 5/5*\n\nEnter tax reliefs (if any):\n(Example: 200000 or 0)\n\n0 - Cancel | # - Menu"
        except:
            return "? Invalid amount. Please enter a valid number"
    
    elif step == 5:
        try:
            reliefs = float(text.replace(',', '')) if text != '0' else 200000
            inputs["reliefs"] = reliefs
            
            cost = TAX_FILING_COSTS["paye_assistance"]
            credit_details = get_credit_details(account_id)
            if int(credit_details.get("balance", 0)) < cost:
                cancel_filing_session(account_id)
                return f"""? *Insufficient Credits*

Need {cost} credits for PAYE filing.
Current balance: {credit_details.get('balance', 0)} credits

Buy top-ups: T10, T50, T100, T500"""
            
            data = calculate_paye(
                inputs["salary"],
                pension_pct=8,
                nhf_pct=2.5,
                allowances=inputs.get("allowances", 0),
                relief=reliefs
            )
            
            success, message = deduct_credits(account_id, cost, "PAYE Filing Assistance")
            if not success:
                cancel_filing_session(account_id)
                return f"? {message}"
            
            reference = f"PAYE_{datetime.now().strftime('%Y%m%d')}_{uuid.uuid4().hex[:6]}"
            result_summary = f"""?? *PAYE FILING SUMMARY*

?? *Employee Details:*
Monthly Salary: ?{inputs['salary']:,.2f}
Pension: ?{inputs.get('pension', 0):,.2f}
NHF: ?{inputs.get('nhf', 0):,.2f}
Allowances: ?{inputs.get('allowances', 0):,.2f}

?? *Tax Calculation:*
Tax: ?{data['tax']:,.0f}
Net: ?{data['net']:,.0f}
Rate: {data['rate']}%

?? *Filing Reference:* {reference}

{DISCLAIMER_FILING}"""
            
            supabase.table("tax_filings").insert({
                "user_id": account_id,
                "tax_type": "PAYE",
                "inputs": inputs,
                "status": "submitted",
                "reference": reference,
                "submitted_at": datetime.now().isoformat(),
                "filing_reference": reference,
                "result_summary": result_summary[:500],
                "credits_used": cost,
                "channel": "whatsapp",
                "updated_at": datetime.now().isoformat()
            }).execute()
            
            cancel_filing_session(account_id)
            return result_summary + "\n\nReply 8 for main menu or 7 for more filing"
            
        except Exception as e:
            cancel_filing_session(account_id)
            return f"? Error processing filing: {str(e)}"
    
    return None

# ============ FILING SESSION MANAGEMENT ============

def create_filing_session(account_id, phone_number, filing_type):
    """Create a new filing session in database"""
    try:
        supabase.table("filing_sessions")\
            .update({"status": "cancelled", "updated_at": datetime.now().isoformat()})\
            .eq("account_id", account_id)\
            .eq("status", "active")\
            .execute()
        
        supabase.table("filing_sessions").insert({
            "account_id": account_id,
            "phone_number": phone_number,
            "filing_type": filing_type,
            "current_step": 1,
            "inputs": {},
            "status": "active",
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat()
        }).execute()
        
        logging.info(f"? Created filing session for {phone_number}: {filing_type}")
        return True
    except Exception as e:
        logging.error(f"Error creating filing session: {e}")
        return False

def get_active_filing_session(account_id):
    """Get active filing session from database"""
    try:
        result = supabase.table("filing_sessions")\
            .select("*")\
            .eq("account_id", account_id)\
            .eq("status", "active")\
            .limit(1)\
            .execute()
        
        if result.data:
            return result.data[0]
        return None
    except Exception as e:
        logging.error(f"Error getting filing session: {e}")
        return None

def update_filing_session(account_id, step, inputs):
    """Update existing filing session"""
    try:
        supabase.table("filing_sessions")\
            .update({
                "current_step": step,
                "inputs": inputs,
                "updated_at": datetime.now().isoformat()
            })\
            .eq("account_id", account_id)\
            .eq("status", "active")\
            .execute()
        return True
    except Exception as e:
        logging.error(f"Error updating filing session: {e}")
        return False

def cancel_filing_session(account_id):
    """Cancel active filing session"""
    try:
        supabase.table("filing_sessions")\
            .update({"status": "cancelled", "updated_at": datetime.now().isoformat()})\
            .eq("account_id", account_id)\
            .eq("status", "active")\
            .execute()
        return True
    except Exception as e:
        logging.error(f"Error cancelling filing session: {e}")
        return False

# ============ ACCOUNT MANAGEMENT ============

def get_canonical_account_id(phone_number):
    """Get or create canonical account_id"""
    if not supabase:
        return None
    
    try:
        account_result = supabase.table("accounts").select("account_id").eq("provider_user_id", str(phone_number)).execute()
        if account_result.data:
            return account_result.data[0].get("account_id")
        
        user_result = supabase.table("bot_users").select("auth_user_id").eq("platform", "whatsapp").eq("user_id", str(phone_number)).execute()
        if user_result.data and user_result.data[0].get("auth_user_id"):
            auth_user_id = user_result.data[0].get("auth_user_id")
            existing = supabase.table("accounts").select("account_id").eq("account_id", auth_user_id).execute()
            if existing.data:
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
        
        return auth_user_id
        
    except Exception as e:
        logging.error(f"Error getting canonical account: {e}")
        return None

def has_active_subscription(account_id):
    """Check if user has active subscription"""
    try:
        result = supabase.table("subscriptions").select("*").eq("account_id", account_id).eq("status", "active").execute()
        if result.data:
            sub = result.data[0]
            expires_at = sub.get("expires_at")
            if expires_at:
                expires_at_clean = expires_at.replace('+00:00', '').replace('Z', '')
                expiry = datetime.fromisoformat(expires_at_clean)
                if expiry < datetime.now():
                    return False
            return True
        return False
    except Exception as e:
        logging.error(f"Error checking subscription: {e}")
        return False

def get_credit_balance(account_id):
    """Get total credit balance"""
    try:
        result = supabase.table("ai_credit_balances").select("balance").eq("account_id", account_id).limit(1).execute()
        if result.data:
            return int(result.data[0].get("balance", 0))
        return 0
    except Exception as e:
        logging.error(f"Error getting balance: {e}")
        return 0

def get_credit_details(account_id):
    """Get detailed credit information"""
    try:
        result = supabase.table("ai_credit_balances").select("*").eq("account_id", account_id).limit(1).execute()
        if result.data:
            return result.data[0]
        return {"balance": 0, "plan_credits": 0, "topup_credits": 0}
    except Exception as e:
        logging.error(f"Error getting credit details: {e}")
        return {"balance": 0, "plan_credits": 0, "topup_credits": 0}

def deduct_credits(account_id, cost, feature_name):
    """Deduct credits from user's balance"""
    try:
        credit_details = get_credit_details(account_id)
        current_topup = int(credit_details.get("topup_credits", 0))
        current_plan = int(credit_details.get("plan_credits", 0))
        current_balance = int(credit_details.get("balance", 0))
        
        if current_balance < cost:
            return False, f"Insufficient credits. Need {cost}, have {current_balance}"
        
        if current_topup >= cost:
            new_topup = current_topup - cost
            new_balance = current_balance - cost
            supabase.table("ai_credit_balances").update({
                "balance": new_balance,
                "topup_credits": new_topup,
                "updated_at": datetime.now().isoformat()
            }).eq("account_id", account_id).execute()
        else:
            remaining = cost - current_topup
            new_balance = current_balance - cost
            new_plan = current_plan - remaining
            supabase.table("ai_credit_balances").update({
                "balance": new_balance,
                "topup_credits": 0,
                "plan_credits": new_plan,
                "updated_at": datetime.now().isoformat()
            }).eq("account_id", account_id).execute()
        
        return True, f"Used {cost} credits for {feature_name}"
    except Exception as e:
        logging.error(f"Error deducting credits: {e}")
        return False, str(e)

def add_topup_credits(account_id, credits, reference):
    """Add top-up credits"""
    if not has_active_subscription(account_id):
        return False, "Active subscription required for top-ups"
    
    try:
        existing = supabase.table("ai_credit_balances").select("*").eq("account_id", account_id).execute()
        
        if existing.data:
            current_balance = int(existing.data[0].get("balance", 0))
            current_topup = int(existing.data[0].get("topup_credits", 0))
            new_topup = current_topup + credits
            new_balance = current_balance + credits
            supabase.table("ai_credit_balances").update({
                "balance": new_balance,
                "topup_credits": new_topup,
                "updated_at": datetime.now().isoformat()
            }).eq("account_id", account_id).execute()
        else:
            supabase.table("ai_credit_balances").insert({
                "account_id": account_id,
                "balance": credits,
                "plan_credits": 0,
                "topup_credits": credits,
                "updated_at": datetime.now().isoformat()
            }).execute()
        
        return True, f"Added {credits} top-up credits"
    except Exception as e:
        return False, str(e)

# ============ TAX CALCULATION FUNCTIONS ============

def calculate_paye(monthly_gross, pension_pct=8, nhf_pct=2.5, allowances=0, relief=200000):
    """Calculate PAYE tax"""
    annual_gross = monthly_gross * 12
    annual_pension = monthly_gross * (pension_pct / 100) * 12
    annual_nhf = monthly_gross * (nhf_pct / 100) * 12
    annual_allowances = allowances * 12
    
    cra_fixed = relief
    cra_one_percent = annual_gross * 0.01
    cra_base = max(cra_fixed, cra_one_percent)
    cra_percentage = annual_gross * 0.20
    cra_total = cra_base + cra_percentage
    
    total_deductions = annual_pension + annual_nhf + annual_allowances + cra_total
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
    net = monthly_gross - (monthly_gross * pension_pct / 100) - (monthly_gross * nhf_pct / 100) - allowances - monthly_tax
    
    return {
        "gross": monthly_gross,
        "pension": round(monthly_gross * pension_pct / 100),
        "nhf": round(monthly_gross * nhf_pct / 100),
        "tax": round(monthly_tax),
        "net": round(net),
        "rate": round((monthly_tax / monthly_gross) * 100, 1)
    }

def calculate_vat(amount, rate=7.5):
    vat = amount * rate / 100
    return {"amount": amount, "rate": rate, "vat": round(vat, 2), "total": round(amount + vat, 2)}

def calculate_cit(revenue, expenses):
    profit = revenue - expenses
    if revenue > 100000000:
        rate = 30
    elif revenue > 25000000:
        rate = 20
    else:
        rate = 0
    cit = profit * rate / 100
    return {"revenue": revenue, "expenses": expenses, "profit": profit, "rate": rate, "cit": round(cit, 2)}

# ============ MESSAGE HANDLERS ============

def get_filing_menu():
    return f"""?? *TAX FILING & MANAGEMENT*

?? *Premium Feature* (Requires Active Subscription)

Reply with:

F1 - PAYE Filing Assistance (10 credits)
F2 - VAT Return Preparation (15 credits)
F3 - CIT Calculation & Filing (20 credits)
F4 - Generate Document (5-10 credits)
F5 - View Filing History
F0 - Back to Main Menu

{DISCLAIMER_FILING}

0 - Cancel | # - Main Menu"""

def get_credit_packages_menu():
    return f"""?? *Buy AI Credits*

?? *Requires Active Subscription*

T10 - 10 credits - ?500
T50 - 50 credits - ?2,000
T100 - 100 credits - ?3,500
T500 - 500 credits - ?15,000

{DISCLAIMER_CREDITS}

0 - Cancel | # - Main Menu"""

def get_plans_list_menu():
    return """?? *AVAILABLE SUBSCRIPTION PLANS*

*STARTER PLANS*
S1 - Starter Monthly - ?5,000/month - 100 credits
S2 - Starter Quarterly - ?14,000/quarter - 300 credits
S3 - Starter Yearly - ?51,000/year - 1,200 credits

*PROFESSIONAL PLANS*
P1 - Professional Monthly - ?12,000/month - 300 credits
P2 - Professional Quarterly - ?33,600/quarter - 900 credits
P3 - Professional Yearly - ?122,400/year - 3,600 credits

*BUSINESS PLANS*
B1 - Business Monthly - ?25,000/month - 800 credits
B2 - Business Quarterly - ?70,000/quarter - 2,400 credits
B3 - Business Yearly - ?255,000/year - 9,600 credits

Reply with plan code (S1, P1, B1, etc.) to subscribe

0 - Cancel | # - Main Menu"""

def get_main_menu():
    return f"""*?? Naija Tax Guide*

1?? - Ask a tax question
2?? - Check credits balance
3?? - Check my subscription
4?? - View subscription plans
5?? - Premium features
6?? - Buy top-up credits
7?? - Tax filing & management
8?? - Help / Menu

*Free Features:*
• CALC 500000 - Calculate PAYE tax
• Database answers (50/day)

*Premium (requires subscription):*
• AI answers (1 credit)
• Tax filing (10-20 credits)

*Commands:* T10, T50, T100, T500 - Buy top-up
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
            logging.error(f"Failed to send: {response.status_code}")
        return False
    except Exception as e:
        logging.error(f"Send error: {e}")
        return False

def process_paye_step(account_id, phone_number, session, text):
    """Process PAYE filing step by step"""
    step = session.get("current_step", 1)
    inputs = session.get("inputs", {})
    
    if step == 1:
        try:
            salary = float(text.replace(',', ''))
            inputs["salary"] = salary
            update_filing_session(account_id, 2, inputs)
            return "?? *PAYE Filing - Step 2/5*\n\nEnter pension contribution (employee):\n(Example: 40000 or 0 if none)\n\n0 - Cancel | # - Menu"
        except:
            return "? Invalid amount. Please enter a valid salary (e.g., 500000)"
    
    elif step == 2:
        try:
            pension = float(text.replace(',', ''))
            inputs["pension"] = pension
            update_filing_session(account_id, 3, inputs)
            return "?? *PAYE Filing - Step 3/5*\n\nEnter NHF contribution (employee):\n(Example: 12500 or 0 if none)\n\n0 - Cancel | # - Menu"
        except:
            return "? Invalid amount. Please enter a valid number"
    
    elif step == 3:
        try:
            nhf = float(text.replace(',', ''))
            inputs["nhf"] = nhf
            update_filing_session(account_id, 4, inputs)
            return "?? *PAYE Filing - Step 4/5*\n\nEnter other allowances (if any):\n(Example: 50000 or 0)\n\n0 - Cancel | # - Menu"
        except:
            return "? Invalid amount. Please enter a valid number"
    
    elif step == 4:
        try:
            allowances = float(text.replace(',', ''))
            inputs["allowances"] = allowances
            update_filing_session(account_id, 5, inputs)
            return "?? *PAYE Filing - Step 5/5*\n\nEnter tax reliefs (if any):\n(Example: 200000 or 0)\n\n0 - Cancel | # - Menu"
        except:
            return "? Invalid amount. Please enter a valid number"
    
    elif step == 5:
        try:
            reliefs = float(text.replace(',', '')) if text != '0' else 200000
            inputs["reliefs"] = reliefs
            
            cost = TAX_FILING_COSTS["paye_assistance"]
            credit_details = get_credit_details(account_id)
            if int(credit_details.get("balance", 0)) < cost:
                cancel_filing_session(account_id)
                return f"""? *Insufficient Credits*

Need {cost} credits for PAYE filing.
Current balance: {credit_details.get('balance', 0)} credits

Buy top-ups: T10, T50, T100, T500"""
            
            data = calculate_paye(
                inputs["salary"],
                pension_pct=8,
                nhf_pct=2.5,
                allowances=inputs.get("allowances", 0),
                relief=reliefs
            )
            
            success, message = deduct_credits(account_id, cost, "PAYE Filing Assistance")
            if not success:
                cancel_filing_session(account_id)
                return f"? {message}"
            
            reference = f"PAYE_{datetime.now().strftime('%Y%m%d')}_{uuid.uuid4().hex[:6]}"
            result_summary = f"""?? *PAYE FILING SUMMARY*

?? *Employee Details:*
Monthly Salary: ?{inputs['salary']:,.2f}
Pension: ?{inputs.get('pension', 0):,.2f}
NHF: ?{inputs.get('nhf', 0):,.2f}
Allowances: ?{inputs.get('allowances', 0):,.2f}

?? *Tax Calculation:*
Tax: ?{data['tax']:,.0f}
Net: ?{data['net']:,.0f}
Rate: {data['rate']}%

?? *Filing Reference:* {reference}

{DISCLAIMER_FILING}"""
            
            supabase.table("tax_filings").insert({
                "user_id": account_id,
                "tax_type": "PAYE",
                "inputs": inputs,
                "status": "submitted",
                "reference": reference,
                "submitted_at": datetime.now().isoformat(),
                "filing_reference": reference,
                "result_summary": result_summary[:500],
                "credits_used": cost,
                "channel": "whatsapp",
                "updated_at": datetime.now().isoformat()
            }).execute()
            
            cancel_filing_session(account_id)
            return result_summary + "\n\nReply 8 for main menu or 7 for more filing"
            
        except Exception as e:
            cancel_filing_session(account_id)
            return f"? Error processing filing: {str(e)}"
    
    return None


# ============ MAIN WEBHOOK ROUTE ============

@bp.route('/whatsapp/webhook', methods=['GET', 'POST'])
def webhook():
    """Main WhatsApp webhook handler"""
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
                    send_whatsapp(from_number, "? Service unavailable. Please try again later.")
                    continue
                
                canonical_account_id = get_canonical_account_id(from_number)
                if not canonical_account_id:
                    send_whatsapp(from_number, "? Error initializing your account. Please try again later.")
                    continue
                
                # ============ GLOBAL COMMANDS ============
                if text == '#':
                    cancel_filing_session(canonical_account_id)
                    user_state.pop(from_number, None)
                    send_whatsapp(from_number, get_main_menu())
                    continue
                
                if text == '0':
                    cancel_filing_session(canonical_account_id)
                    user_state.pop(from_number, None)
                    send_whatsapp(from_number, "? Cancelled.\n\nReply 8 for main menu.")
                    continue
                
                if text == '*':
                    cancel_filing_session(canonical_account_id)
                    user_state.pop(from_number, None)
                    send_whatsapp(from_number, get_main_menu())
                    continue
                
                # ============ CALCULATOR COMMAND ============
                if text.upper().startswith('CALC'):
                    parts = text[4:].strip().split()
                    if not parts:
                        send_whatsapp(from_number, f"""?? *Tax Calculator*

Examples:
• CALC 500000 - Calculate PAYE tax
• CALC VAT 100000 - Calculate VAT
• CALC CIT 50000000 20000000 - Calculate CIT

{DISCLAIMER_CALC}""")
                        continue
                    
                    cmd = parts[0].upper()
                    
                    if cmd == 'VAT' and len(parts) >= 2:
                        try:
                            amount = float(parts[1].replace(',', ''))
                            result = calculate_vat(amount)
                            send_whatsapp(from_number, f"""?? *VAT CALCULATION*

Amount: ?{result['amount']:,.2f}
VAT ({result['rate']}%): ?{result['vat']:,.2f}
Total: *?{result['total']:,.2f}*

{DISCLAIMER_CALC}""")
                        except:
                            send_whatsapp(from_number, "? Invalid amount. Example: CALC VAT 100000")
                    
                    elif cmd == 'CIT' and len(parts) >= 3:
                        try:
                            revenue = float(parts[1].replace(',', ''))
                            expenses = float(parts[2].replace(',', ''))
                            result = calculate_cit(revenue, expenses)
                            send_whatsapp(from_number, f"""?? *CIT CALCULATION*

Revenue: ?{result['revenue']:,.2f}
Expenses: ?{result['expenses']:,.2f}
Profit: ?{result['profit']:,.2f}
Rate: {result['rate']}%
CIT Payable: *?{result['cit']:,.2f}*

{DISCLAIMER_CALC}""")
                        except:
                            send_whatsapp(from_number, "? Invalid format. Example: CALC CIT 50000000 20000000")
                    
                    else:
                        try:
                            amount = float(parts[0].replace(',', ''))
                            result = calculate_paye(amount)
                            send_whatsapp(from_number, f"""?? *PAYE CALCULATION*

Gross: ?{result['gross']:,.0f}
Pension: ?{result['pension']:,.0f}
NHF: ?{result['nhf']:,.0f}
Tax: ?{result['tax']:,.0f}
Net: *?{result['net']:,.0f}*
Rate: {result['rate']}%

{DISCLAIMER_CALC}""")
                        except:
                            send_whatsapp(from_number, "? Invalid amount. Example: CALC 500000")
                    continue
                
                # ============ T-CODES (Top-up) ============
                t_code = text.upper().strip()
                if t_code in ["T10", "T50", "T100", "T500"]:
                    if not has_active_subscription(canonical_account_id):
                        send_whatsapp(from_number, "? Active subscription required for top-ups. Reply 4 to view plans.")
                        continue
                    
                    package = CREDIT_PACKAGES.get(t_code)
                    if package:
                        reference = f"CREDIT_{package['credits']}_{uuid.uuid4().hex[:8]}"
                        amount_kobo = package["amount_kobo"]
                        PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY")
                        base_url = os.getenv("PUBLIC_BACKEND_BASE_URL", "https://incredible-nonie-bmsconcept-37359733.koyeb.app")
                        callback_url = f"{base_url}/payment/success?phone={from_number}&type=credits&credits={package['credits']}"
                        
                        payload = {
                            "amount": amount_kobo,
                            "email": f"wa_{from_number}@temp.ng",
                            "reference": reference,
                            "currency": "NGN",
                            "metadata": {
                                "account_id": canonical_account_id,
                                "credits": package['credits'],
                                "package_code": t_code,
                                "type": "credit_purchase",
                                "channel_type": "whatsapp",
                                "provider_user_id": from_number,
                                "amount_ngn": package["amount_ngn"]
                            },
                            "callback_url": callback_url
                        }
                        
                        headers = {"Authorization": f"Bearer {PAYSTACK_SECRET_KEY}", "Content-Type": "application/json"}
                        
                        try:
                            response = requests.post(f"https://api.paystack.co/transaction/initialize", json=payload, headers=headers, timeout=30)
                            if response.status_code == 200:
                                data = response.json()
                                if data.get("status"):
                                    send_whatsapp(from_number, f"""?? *Payment Link*

Package: {package['description']}
Amount: ?{package['amount_ngn']:,}

?? {data['data']['authorization_url']}

Reference: {reference}

{DISCLAIMER_CREDITS}
0 - Cancel""")
                                else:
                                    send_whatsapp(from_number, "? Payment initialization failed.")
                            else:
                                send_whatsapp(from_number, "? Payment service error.")
                        except Exception as e:
                            logging.error(f"Payment error: {e}")
                            send_whatsapp(from_number, "? Failed to generate payment link.")
                    continue
                
                # ============ OPTION 7 - TAX FILING ============
                if text == '7':
                    if not has_active_subscription(canonical_account_id):
                        send_whatsapp(from_number, f"? Tax filing requires active subscription. Reply 4 to view plans.\n\n{DISCLAIMER_FILING}")
                        continue
                    
                    cancel_filing_session(canonical_account_id)
                    send_whatsapp(from_number, get_filing_menu())
                    continue
                
                # ============ HANDLE ACTIVE FILING SESSION ============
                active_session = get_active_filing_session(canonical_account_id)
                
                if active_session:
                    filing_type = active_session.get("filing_type")
                    
                    if text.upper() == 'F0' or text == '#':
                        cancel_filing_session(canonical_account_id)
                        send_whatsapp(from_number, get_main_menu())
                        continue
                    
                    if text == '0':
                        cancel_filing_session(canonical_account_id)
                        send_whatsapp(from_number, "? Filing cancelled.\n\nReply 8 for main menu.")
                        continue
                    
                    if filing_type == "PAYE":
                        response = process_paye_step(canonical_account_id, from_number, active_session, text)
                        if response:
                            send_whatsapp(from_number, response)
                        continue
                    
                    elif filing_type == "VAT":
                        send_whatsapp(from_number, "?? VAT filing coming soon. Use F1 for PAYE.")
                        cancel_filing_session(canonical_account_id)
                        continue
                    
                    elif filing_type == "CIT":
                        send_whatsapp(from_number, "?? CIT filing coming soon. Use F1 for PAYE.")
                        cancel_filing_session(canonical_account_id)
                        continue
                
                # ============ FILING CODES (F1, F2, etc.) ============
                if text.upper() == 'F1':
                    if not has_active_subscription(canonical_account_id):
                        send_whatsapp(from_number, "? Tax filing requires active subscription. Reply 4 to view plans.")
                        continue
                    
                    credit_details = get_credit_details(canonical_account_id)
                    if int(credit_details.get("balance", 0)) < TAX_FILING_COSTS["paye_assistance"]:
                        send_whatsapp(from_number, f"""? *Insufficient Credits*

Need {TAX_FILING_COSTS['paye_assistance']} credits for PAYE filing.
Current balance: {credit_details.get('balance', 0)} credits

Buy top-ups: T10, T50, T100, T500""")
                        continue
                    
                    if create_filing_session(canonical_account_id, from_number, "PAYE"):
                        send_whatsapp(from_number, "?? *PAYE Filing - Step 1/5*\n\nEnter employee's monthly salary:\n(Example: 500000)\n\n0 - Cancel | # - Menu")
                    else:
                        send_whatsapp(from_number, "? Error starting filing. Please try again.")
                    continue
                
                if text.upper() == 'F2':
                    if not has_active_subscription(canonical_account_id):
                        send_whatsapp(from_number, "? Tax filing requires active subscription. Reply 4 to view plans.")
                        continue
                    send_whatsapp(from_number, "?? VAT filing coming soon. Use F1 for PAYE.")
                    continue
                
                if text.upper() == 'F3':
                    if not has_active_subscription(canonical_account_id):
                        send_whatsapp(from_number, "? Tax filing requires active subscription. Reply 4 to view plans.")
                        continue
                    send_whatsapp(from_number, "?? CIT filing coming soon. Use F1 for PAYE.")
                    continue
                
                if text.upper() == 'F4':
                    if not has_active_subscription(canonical_account_id):
                        send_whatsapp(from_number, "? Document generation requires active subscription. Reply 4 to view plans.")
                        continue
                    
                    credit_details = get_credit_details(canonical_account_id)
                    if int(credit_details.get("balance", 0)) < 5:
                        send_whatsapp(from_number, f"""? *Insufficient Credits*

Need at least 5 credits for document generation.
Current balance: {credit_details.get('balance', 0)} credits

Buy top-ups: T10, T50, T100, T500""")
                        continue
                    
                    send_whatsapp(from_number, f"""?? *Document Generation*

Select document type:

F4-1 - Tax Payment Receipt (5 credits)
F4-2 - PAYE Filing Form (5 credits)
F4-3 - VAT Return Form (5 credits)
F4-4 - CIT Computation Report (10 credits)
F4-5 - Annual Tax Summary (10 credits)

{DISCLAIMER_DOC}

0 - Cancel""")
                    continue
                
                if text.upper().startswith('F4-'):
                    doc_num = text.upper().replace('F4-', '')
                    doc_costs = {"1": 5, "2": 5, "3": 5, "4": 10, "5": 10}
                    doc_names = {"1": "Tax Payment Receipt", "2": "PAYE Filing Form", "3": "VAT Return Form", "4": "CIT Computation Report", "5": "Annual Tax Summary"}
                    
                    if doc_num in doc_costs:
                        if not has_active_subscription(canonical_account_id):
                            send_whatsapp(from_number, "? Document generation requires active subscription.")
                            continue
                        
                        cost = doc_costs[doc_num]
                        credit_details = get_credit_details(canonical_account_id)
                        if int(credit_details.get("balance", 0)) < cost:
                            send_whatsapp(from_number, f"? Need {cost} credits. Balance: {credit_details.get('balance', 0)}")
                            continue
                        
                        success, message = deduct_credits(canonical_account_id, cost, f"Document: {doc_names[doc_num]}")
                        if success:
                            doc_ref = f"DOC_{doc_names[doc_num].replace(' ', '_')}_{uuid.uuid4().hex[:8]}"
                            send_whatsapp(from_number, f"""?? *Document Generated*

?? Type: {doc_names[doc_num]}
?? Reference: {doc_ref}
?? Credits Used: {cost}

{DISCLAIMER_DOC}

Reply 8 for main menu""")
                        else:
                            send_whatsapp(from_number, f"? {message}")
                    continue
                
                if text.upper() == 'F5':
                    history_result = supabase.table("tax_filings")\
                        .select("filing_reference, tax_type, credits_used, status, submitted_at")\
                        .eq("user_id", canonical_account_id)\
                        .eq("channel", "whatsapp")\
                        .order("submitted_at", desc=True)\
                        .limit(10)\
                        .execute()
                    
                    if not history_result.data:
                        send_whatsapp(from_number, "?? *Filing History*\n\nNo filings found.\n\nStart a filing with 7 then F1.")
                    else:
                        history = "?? *Filing History*\n\n"
                        for filing in history_result.data[:5]:
                            history += f"• {filing.get('tax_type', 'Unknown')}: {filing.get('filing_reference', 'N/A')}\n  ?? {filing.get('submitted_at', '')[:10]} | ?? {filing.get('credits_used', 0)} credits\n\n"
                        history += f"\n{DISCLAIMER_FILING}"
                        send_whatsapp(from_number, history)
                    continue
                
                if text.upper() == 'F0':
                    send_whatsapp(from_number, get_main_menu())
                    continue
                
                # ============ OPTION 6 - BUY CREDITS MENU ============
                if text == '6':
                    if not has_active_subscription(canonical_account_id):
                        send_whatsapp(from_number, "? Active subscription required for top-ups. Reply 4 to view plans.")
                        continue
                    send_whatsapp(from_number, get_credit_packages_menu())
                    continue
                
                # ============ OPTION 4 - VIEW PLANS ============
                if text == '4':
                    send_whatsapp(from_number, get_plans_list_menu())
                    continue
                
                # ============ OPTION 8 - MAIN MENU ============
                if text == '8':
                    send_whatsapp(from_number, get_main_menu())
                    continue
                
                # ============ OPTION 3 - CHECK SUBSCRIPTION ============
                if text == '3':
                    subscription = supabase.table("subscriptions").select("*").eq("account_id", canonical_account_id).eq("status", "active").execute()
                    credit_details = get_credit_details(canonical_account_id)
                    if subscription.data:
                        sub = subscription.data[0]
                        expires_at = sub.get("expires_at", "N/A")
                        if expires_at != "N/A":
                            expires_at = expires_at[:10]
                        send_whatsapp(from_number, f"""?? *YOUR SUBSCRIPTION*

? ACTIVE
?? Expires: {expires_at}
?? Balance: {credit_details.get('balance', 0)} credits
• Top-up: {credit_details.get('topup_credits', 0)}
• Plan: {credit_details.get('plan_credits', 0)}

{DISCLAIMER_SUBSCRIPTION}""")
                    else:
                        send_whatsapp(from_number, f"""?? *NO ACTIVE SUBSCRIPTION*

Free Plan:
• Database answers: 50/day
• Tax calculations: unlimited (use CALC command)

Reply 4 to view plans

{DISCLAIMER_MAIN}""")
                    continue
                
                # ============ OPTION 2 - CHECK BALANCE ============
                if text == '2':
                    credit_details = get_credit_details(canonical_account_id)
                    send_whatsapp(from_number, f"""?? *Credit Balance*

Total: *{credit_details.get('balance', 0)}* credits
• Top-up: {credit_details.get('topup_credits', 0)} (used first)
• Plan: {credit_details.get('plan_credits', 0)}

{DISCLAIMER_CREDITS}""")
                    continue
                
                # ============ OPTION 1 - ASK QUESTION ============
                if text == '1':
                    user_state[from_number] = {"step": "asking_question", "timestamp": current_time}
                    send_whatsapp(from_number, "?? Please type your tax question.\n\n# - Menu | 0 - Cancel")
                    continue
                
                # ============ OPTION 5 - PREMIUM FEATURES ============
                if text == '5':
                    send_whatsapp(from_number, f"""?? *Premium Features*

? With active subscription:
• AI answers (1 credit)
• PAYE filing (10 credits)
• VAT filing (15 credits)
• CIT filing (20 credits)
• Document generation (5-10 credits)

{DISCLAIMER_MAIN}""")
                    continue
                
                # ============ HANDLE ASKING QUESTION STATE ============
                if from_number in user_state and user_state[from_number].get("step") == "asking_question":
                    try:
                        from app.services.ask_service import ask_guarded
                        SERVICES_AVAILABLE = True
                    except:
                        SERVICES_AVAILABLE = False
                    
                    if SERVICES_AVAILABLE:
                        if not has_active_subscription(canonical_account_id):
                            balance = get_credit_balance(canonical_account_id)
                            if balance <= 0:
                                send_whatsapp(from_number, f"? AI answers require active subscription or credits.\n\nBuy top-ups: T10, T50, T100, T500\nSubscribe: Reply 4")
                                user_state.pop(from_number, None)
                                continue
                        
                        result = ask_guarded({
                            "question": text,
                            "account_id": canonical_account_id,
                            "lang": "en",
                            "channel": "whatsapp"
                        })
                        
                        if result.get("ok"):
                            answer = result.get("answer", "")
                            new_balance = get_credit_balance(canonical_account_id)
                            send_whatsapp(from_number, f"{answer}\n\n---\n?? *Credits remaining:* {new_balance}\n\n{DISCLAIMER_AI}\n\nReply 1 for another question or 8 for menu.")
                        else:
                            send_whatsapp(from_number, f"? {result.get('error', 'Unknown error')}\n\n{DISCLAIMER_AI}")
                    else:
                        send_whatsapp(from_number, "? AI service unavailable.")
                    user_state.pop(from_number, None)
                    continue
                
                # ============ DEFAULT - SEND MAIN MENU ============
                send_whatsapp(from_number, get_main_menu())
        
        return "ok"
    except Exception as e:
        logging.error(f"Error in webhook: {e}")
        return "error", 500

