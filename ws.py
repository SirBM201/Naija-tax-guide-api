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

# ============ REGISTER WEB BLUEPRINTS (ADDED) ============
# This enables all web API routes (workspace, auth, billing, etc.)
try:
    from app.routes.workspace import bp as workspace_bp
    from app.routes.web_auth import bp as web_auth_bp
    from app.routes.web_ask import bp as web_ask_bp
    from app.routes.billing import bp as billing_bp
    from app.routes.me import bp as me_bp
    from app.routes.plans import bp as plans_bp
    from app.routes.referrals import bp as referrals_bp
    from app.routes.history import bp as history_bp
    from app.routes.web_session import bp as web_session_bp
    from app.routes.accounts import bp as accounts_bp
    from app.routes.tax import bp as tax_bp
    from app.routes.subscriptions import bp as subscriptions_bp
    
    app.register_blueprint(workspace_bp, url_prefix='/api')
    app.register_blueprint(web_auth_bp, url_prefix='/api/web/auth')
    app.register_blueprint(web_ask_bp, url_prefix='/api/web')
    app.register_blueprint(billing_bp, url_prefix='/api/billing')
    app.register_blueprint(me_bp, url_prefix='/api')
    app.register_blueprint(plans_bp, url_prefix='/api')
    app.register_blueprint(referrals_bp, url_prefix='/api')
    app.register_blueprint(history_bp, url_prefix='/api')
    app.register_blueprint(web_session_bp, url_prefix='/api')
    app.register_blueprint(accounts_bp, url_prefix='/api')
    app.register_blueprint(tax_bp, url_prefix='/api')
    app.register_blueprint(subscriptions_bp, url_prefix='/api')
    
    print("✅ Web blueprints registered successfully")
    print("   Available web endpoints:")
    print("   • GET  /api/workspace/limits")
    print("   • POST /api/web/auth/request-otp")
    print("   • POST /api/web/auth/verify-otp")
    print("   • POST /api/web/ask")
    print("   • GET  /api/me")
    print("   • GET  /api/billing/me")
    print("   • GET  /api/plans")
except ImportError as e:
    print(f"⚠️ Could not import web blueprints: {e}")
except Exception as e:
    print(f"⚠️ Error registering web blueprints: {e}")
# ============ END WEB BLUEPRINT REGISTRATION ==========

# ============ LEGAL DISCLAIMERS ============
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

# Credit packages
CREDIT_PACKAGES = {
    "T10": {"credits": 10, "amount_ngn": 500, "amount_kobo": 50000, "code": "T10", "description": "10 AI Credits", "requires_subscription": True},
    "T50": {"credits": 50, "amount_ngn": 2000, "amount_kobo": 200000, "code": "T50", "description": "50 AI Credits", "requires_subscription": True},
    "T100": {"credits": 100, "amount_ngn": 3500, "amount_kobo": 350000, "code": "T100", "description": "100 AI Credits", "requires_subscription": True},
    "T500": {"credits": 500, "amount_ngn": 15000, "amount_kobo": 1500000, "code": "T500", "description": "500 AI Credits", "requires_subscription": True},
}

DEFAULT_CREDIT_COSTS = {
    "ai_question": 1,
    "paye_filing": 10,
    "vat_filing": 15,
    "cit_filing": 20,
    "document_simple": 5,
    "document_complex": 10
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

# ============ FILING SESSION MANAGEMENT ============

def create_filing_session(account_id, phone_number, filing_type):
    """Create a new filing session in database"""
    try:
        # Clear any existing active session
        supabase.table("filing_sessions")\
            .update({"status": "cancelled", "updated_at": datetime.now().isoformat()})\
            .eq("account_id", account_id)\
            .eq("status", "active")\
            .execute()
        
        # Create new session
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
        
        logging.info(f"✅ Created filing session for {phone_number}: {filing_type}")
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
        # Check existing account
        account_result = supabase.table("accounts").select("account_id").eq("provider_user_id", str(phone_number)).execute()
        if account_result.data:
            return account_result.data[0].get("account_id")
        
        # Check bot_users
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
        
        # Create new user
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
    """Check if user has active subscription - FIXED timezone issue"""
    try:
        result = supabase.table("subscriptions").select("*").eq("account_id", account_id).eq("status", "active").execute()
        if result.data:
            sub = result.data[0]
            expires_at = sub.get("expires_at")
            if expires_at:
                # Remove timezone info for safe comparison
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

# ============ TAX CALCULATION ============

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

# ============ FILING HANDLERS ============

def get_filing_menu():
    return f"""📋 *TAX FILING & MANAGEMENT*

⚠️ *Premium Feature* (Requires Active Subscription)

Reply with:

F1 - PAYE Filing Assistance (10 credits)
F2 - VAT Return Preparation (15 credits)
F3 - CIT Calculation & Filing (20 credits)
F4 - Generate Document (5-10 credits)
F5 - View Filing History
F0 - Back to Main Menu

{DISCLAIMER_FILING}

0 - Cancel | # - Main Menu"""

def process_paye_step(account_id, phone_number, session, text):
    """Process PAYE filing step by step"""
    step = session.get("current_step", 1)
    inputs = session.get("inputs", {})
    
    if step == 1:
        try:
            salary = float(text.replace(',', ''))
            inputs["salary"] = salary
            update_filing_session(account_id, 2, inputs)
            return "📋 *PAYE Filing - Step 2/5*\n\nEnter pension contribution (employee):\n(Example: 40000 or 0 if none)\n\n0 - Cancel | # - Menu"
        except:
            return "❌ Invalid amount. Please enter a valid salary (e.g., 500000)"
    
    elif step == 2:
        try:
            pension = float(text.replace(',', ''))
            inputs["pension"] = pension
            update_filing_session(account_id, 3, inputs)
            return "📋 *PAYE Filing - Step 3/5*\n\nEnter NHF contribution (employee):\n(Example: 12500 or 0 if none)\n\n0 - Cancel | # - Menu"
        except:
            return "❌ Invalid amount. Please enter a valid number"
    
    elif step == 3:
        try:
            nhf = float(text.replace(',', ''))
            inputs["nhf"] = nhf
            update_filing_session(account_id, 4, inputs)
            return "📋 *PAYE Filing - Step 4/5*\n\nEnter other allowances (if any):\n(Example: 50000 or 0)\n\n0 - Cancel | # - Menu"
        except:
            return "❌ Invalid amount. Please enter a valid number"
    
    elif step == 4:
        try:
            allowances = float(text.replace(',', ''))
            inputs["allowances"] = allowances
            update_filing_session(account_id, 5, inputs)
            return "📋 *PAYE Filing - Step 5/5*\n\nEnter tax reliefs (if any):\n(Example: 200000 or 0)\n\n0 - Cancel | # - Menu"
        except:
            return "❌ Invalid amount. Please enter a valid number"
    
    elif step == 5:
        try:
            reliefs = float(text.replace(',', '')) if text != '0' else 200000
            inputs["reliefs"] = reliefs
            
            # Check credits
            cost = TAX_FILING_COSTS["paye_assistance"]
            credit_details = get_credit_details(account_id)
            if int(credit_details.get("balance", 0)) < cost:
                cancel_filing_session(account_id)
                return f"""❌ *Insufficient Credits*

Need {cost} credits for PAYE filing.
Current balance: {credit_details.get('balance', 0)} credits

Buy top-ups: T10, T50, T100, T500"""
            
            # Calculate result
            data = calculate_paye(
                inputs["salary"],
                pension_pct=8,
                nhf_pct=2.5,
                allowances=inputs.get("allowances", 0),
                relief=reliefs
            )
            
            # Deduct credits
            success, message = deduct_credits(account_id, cost, "PAYE Filing Assistance")
            if not success:
                cancel_filing_session(account_id)
                return f"❌ {message}"
            
            # Save filing record
            reference = f"PAYE_{datetime.now().strftime('%Y%m%d')}_{uuid.uuid4().hex[:6]}"
            result_summary = f"""📋 *PAYE FILING SUMMARY*

📊 *Employee Details:*
Monthly Salary: ₦{inputs['salary']:,.2f}
Pension: ₦{inputs.get('pension', 0):,.2f}
NHF: ₦{inputs.get('nhf', 0):,.2f}
Allowances: ₦{inputs.get('allowances', 0):,.2f}

💰 *Tax Calculation:*
Tax: ₦{data['tax']:,.0f}
Net: ₦{data['net']:,.0f}
Rate: {data['rate']}%

📝 *Filing Reference:* {reference}

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
            return f"❌ Error processing filing: {str(e)}"
    
    return None

# ============ WEBHOOK RESPONSE HELPERS ============

def get_credit_packages_menu():
    return f"""💎 *Buy AI Credits*

⚠️ *Requires Active Subscription*

T10 - 10 credits - ₦500
T50 - 50 credits - ₦2,000
T100 - 100 credits - ₦3,500
T500 - 500 credits - ₦15,000

{DISCLAIMER_CREDITS}

0 - Cancel | # - Main Menu"""

def get_plans_list_menu():
    return """📋 *AVAILABLE SUBSCRIPTION PLANS*

*STARTER PLANS*
S1 - Starter Monthly - ₦5,000/month - 100 credits
S2 - Starter Quarterly - ₦14,000/quarter - 300 credits
S3 - Starter Yearly - ₦51,000/year - 1,200 credits

*PROFESSIONAL PLANS*
P1 - Professional Monthly - ₦12,000/month - 300 credits
P2 - Professional Quarterly - ₦33,600/quarter - 900 credits
P3 - Professional Yearly - ₦122,400/year - 3,600 credits

*BUSINESS PLANS*
B1 - Business Monthly - ₦25,000/month - 800 credits
B2 - Business Quarterly - ₦70,000/quarter - 2,400 credits
B3 - Business Yearly - ₦255,000/year - 9,600 credits

Reply with plan code (S1, P1, B1, etc.) to subscribe

0 - Cancel | # - Main Menu"""

def get_main_menu():
    return f"""*🤖 Naija Tax Guide*

1️⃣ - Ask a tax question
2️⃣ - Check credits balance
3️⃣ - Check my subscription
4️⃣ - View subscription plans
5️⃣ - Premium features
6️⃣ - Buy top-up credits
7️⃣ - Tax filing & management
8️⃣ - Help / Menu

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
                existing_tx = supabase.table("paystack_transactions").select("status").eq("reference", reference).execute()
                if existing_tx.data and existing_tx.data[0].get("status") == 'success':
                    return "OK - Already Processed", 200
            except:
                pass
            
            if transaction_type == 'credit_purchase':
                account_id = metadata.get('account_id')
                credits = metadata.get('credits', 0)
                phone_number = metadata.get('provider_user_id')
                amount = data.get('amount', 0) / 100
                
                success, message = add_topup_credits(account_id, credits, reference)
                
                if success and phone_number:
                    supabase.table("paystack_transactions").update({
                        "status": "success",
                        "updated_at": datetime.now().isoformat()
                    }).eq("reference", reference).execute()
                    
                    credit_details = get_credit_details(account_id)
                    send_whatsapp(phone_number, f"""✅ *CREDITS ADDED SUCCESSFULLY!*

💎 *{credits} top-up credits* added.

💰 Amount: ₦{amount:,.2f}
📊 New balance: *{credit_details.get('balance', 0)}* credits

{DISCLAIMER_CREDITS}

Reply 1 for tax questions or 8 for menu.""")
            
            elif transaction_type == 'subscription':
                phone_number = metadata.get('phone')
                plan_name = metadata.get('plan_name', 'Subscription')
                amount = data.get('amount', 0) / 100
                plan_code = metadata.get('plan_code')
                
                if phone_number:
                    send_whatsapp(phone_number, f"""✅ *PAYMENT SUCCESSFUL!*

🎉 Subscribed to {plan_name}!
💰 Amount: ₦{amount:,.2f}

{DISCLAIMER_SUBSCRIPTION}

Reply 8 for menu.""")
                    
                    canonical_account_id = get_canonical_account_id(phone_number)
                    if canonical_account_id:
                        plan_details = None
                        plans_result = supabase.table("plans").select("*").eq("active", True).execute()
                        for p in (plans_result.data or []):
                            if p.get("plan_code") == plan_code:
                                plan_details = p
                                break
                        
                        plan_credits = plan_details.get("ai_credits_total", 0) if plan_details else 0
                        duration_days = plan_details.get("duration_days", 30) if plan_details else 30
                        expires_at = datetime.now() + timedelta(days=duration_days)
                        
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
        
        return "OK", 200
    except Exception as e:
        logging.error(f"Webhook error: {e}")
        return "Error", 500

SUCCESS_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Payment Successful - Naija Tax Guide</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); }
        .container { text-align: center; background: white; padding: 40px; border-radius: 20px; box-shadow: 0 20px 60px rgba(0,0,0,0.3); max-width: 90%; width: 400px; }
        .success-icon { width: 80px; height: 80px; background: #4CAF50; border-radius: 50%; display: flex; align-items: center; justify-content: center; margin: 0 auto 20px; }
        .success-icon svg { width: 50px; height: 50px; fill: white; }
        h1 { color: #333; margin-bottom: 10px; }
        p { color: #666; margin-bottom: 20px; }
        .plan-name { background: #f0f0f0; padding: 10px; border-radius: 10px; margin: 20px 0; font-weight: bold; }
        .redirect-timer { color: #999; font-size: 14px; margin-top: 20px; }
        .whatsapp-button { display: inline-block; background: #25D366; color: white; padding: 12px 24px; border-radius: 30px; text-decoration: none; margin-top: 20px; font-weight: bold; }
    </style>
</head>
<body>
    <div class="container">
        <div class="success-icon"><svg viewBox="0 0 24 24"><path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41L9 16.17z"/></svg></div>
        <h1>✅ Payment Successful!</h1>
        <p>Your {{ type }} has been processed successfully.</p>
        <div class="plan-name">🎯 {{ amount_info }}</div>
        <div class="redirect-timer">Redirecting to WhatsApp in <span id="countdown">3</span> seconds...</div>
        <a href="{{ whatsapp_url }}" class="whatsapp-button">💬 Return to WhatsApp Now</a>
    </div>
    <script>
        let seconds = 3;
        const timer = setInterval(function() { seconds--; document.getElementById('countdown').textContent = seconds; if (seconds <= 0) { clearInterval(timer); window.location.href = "{{ whatsapp_url }}"; } }, 1000);
    </script>
</body>
</html>
"""

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
                    cancel_filing_session(canonical_account_id)
                    user_state.pop(from_number, None)
                    send_whatsapp(from_number, get_main_menu())
                    continue
                
                if text == '0':
                    cancel_filing_session(canonical_account_id)
                    user_state.pop(from_number, None)
                    send_whatsapp(from_number, "❌ Cancelled.\n\nReply 8 for main menu.")
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
                        send_whatsapp(from_number, f"""📊 *Tax Calculator*

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
                            send_whatsapp(from_number, f"""📊 *VAT CALCULATION*

Amount: ₦{result['amount']:,.2f}
VAT ({result['rate']}%): ₦{result['vat']:,.2f}
Total: *₦{result['total']:,.2f}*

{DISCLAIMER_CALC}""")
                        except:
                            send_whatsapp(from_number, "❌ Invalid amount. Example: CALC VAT 100000")
                    
                    elif cmd == 'CIT' and len(parts) >= 3:
                        try:
                            revenue = float(parts[1].replace(',', ''))
                            expenses = float(parts[2].replace(',', ''))
                            result = calculate_cit(revenue, expenses)
                            send_whatsapp(from_number, f"""📊 *CIT CALCULATION*

Revenue: ₦{result['revenue']:,.2f}
Expenses: ₦{result['expenses']:,.2f}
Profit: ₦{result['profit']:,.2f}
Rate: {result['rate']}%
CIT Payable: *₦{result['cit']:,.2f}*

{DISCLAIMER_CALC}""")
                        except:
                            send_whatsapp(from_number, "❌ Invalid format. Example: CALC CIT 50000000 20000000")
                    
                    else:
                        try:
                            amount = float(parts[0].replace(',', ''))
                            result = calculate_paye(amount)
                            send_whatsapp(from_number, f"""📊 *PAYE CALCULATION*

Gross: ₦{result['gross']:,.0f}
Pension: ₦{result['pension']:,.0f}
NHF: ₦{result['nhf']:,.0f}
Tax: ₦{result['tax']:,.0f}
Net: *₦{result['net']:,.0f}*
Rate: {result['rate']}%

{DISCLAIMER_CALC}""")
                        except:
                            send_whatsapp(from_number, "❌ Invalid amount. Example: CALC 500000")
                    continue
                
                # ============ T-CODES (Top-up) ============
                t_code = text.upper().strip()
                if t_code in ["T10", "T50", "T100", "T500"]:
                    if not has_active_subscription(canonical_account_id):
                        send_whatsapp(from_number, "❌ Active subscription required for top-ups. Reply 4 to view plans.")
                        continue
                    
                    package = CREDIT_PACKAGES.get(t_code)
                    if package:
                        reference = f"CREDIT_{package['credits']}_{uuid.uuid4().hex[:8]}"
                        amount_kobo = package["amount_kobo"]
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
                                    send_whatsapp(from_number, f"""💎 *Payment Link*

Package: {package['description']}
Amount: ₦{package['amount_ngn']:,}

🔗 {data['data']['authorization_url']}

Reference: {reference}

{DISCLAIMER_CREDITS}
0 - Cancel""")
                                else:
                                    send_whatsapp(from_number, "❌ Payment initialization failed.")
                            else:
                                send_whatsapp(from_number, "❌ Payment service error.")
                        except Exception as e:
                            logging.error(f"Payment error: {e}")
                            send_whatsapp(from_number, "❌ Failed to generate payment link.")
                    continue
                
                # ============ OPTION 7 - TAX FILING ============
                if text == '7':
                    if not has_active_subscription(canonical_account_id):
                        send_whatsapp(from_number, f"❌ Tax filing requires active subscription. Reply 4 to view plans.\n\n{DISCLAIMER_FILING}")
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
                        send_whatsapp(from_number, "❌ Filing cancelled.\n\nReply 8 for main menu.")
                        continue
                    
                    if filing_type == "PAYE":
                        response = process_paye_step(canonical_account_id, from_number, active_session, text)
                        if response:
                            send_whatsapp(from_number, response)
                        continue
                    
                    elif filing_type == "VAT":
                        send_whatsapp(from_number, "📋 VAT filing coming soon. Use F1 for PAYE.")
                        cancel_filing_session(canonical_account_id)
                        continue
                    
                    elif filing_type == "CIT":
                        send_whatsapp(from_number, "📋 CIT filing coming soon. Use F1 for PAYE.")
                        cancel_filing_session(canonical_account_id)
                        continue
                
                # ============ FILING CODES (F1, F2, etc.) ============
                if text.upper() == 'F1':
                    if not has_active_subscription(canonical_account_id):
                        send_whatsapp(from_number, "❌ Tax filing requires active subscription. Reply 4 to view plans.")
                        continue
                    
                    credit_details = get_credit_details(canonical_account_id)
                    if int(credit_details.get("balance", 0)) < TAX_FILING_COSTS["paye_assistance"]:
                        send_whatsapp(from_number, f"""❌ *Insufficient Credits*

Need {TAX_FILING_COSTS['paye_assistance']} credits for PAYE filing.
Current balance: {credit_details.get('balance', 0)} credits

Buy top-ups: T10, T50, T100, T500""")
                        continue
                    
                    if create_filing_session(canonical_account_id, from_number, "PAYE"):
                        send_whatsapp(from_number, "📋 *PAYE Filing - Step 1/5*\n\nEnter employee's monthly salary:\n(Example: 500000)\n\n0 - Cancel | # - Menu")
                    else:
                        send_whatsapp(from_number, "❌ Error starting filing. Please try again.")
                    continue
                
                if text.upper() == 'F2':
                    if not has_active_subscription(canonical_account_id):
                        send_whatsapp(from_number, "❌ Tax filing requires active subscription. Reply 4 to view plans.")
                        continue
                    send_whatsapp(from_number, "📋 VAT filing coming soon. Use F1 for PAYE.")
                    continue
                
                if text.upper() == 'F3':
                    if not has_active_subscription(canonical_account_id):
                        send_whatsapp(from_number, "❌ Tax filing requires active subscription. Reply 4 to view plans.")
                        continue
                    send_whatsapp(from_number, "📋 CIT filing coming soon. Use F1 for PAYE.")
                    continue
                
                if text.upper() == 'F4':
                    if not has_active_subscription(canonical_account_id):
                        send_whatsapp(from_number, "❌ Document generation requires active subscription. Reply 4 to view plans.")
                        continue
                    
                    credit_details = get_credit_details(canonical_account_id)
                    if int(credit_details.get("balance", 0)) < 5:
                        send_whatsapp(from_number, f"""❌ *Insufficient Credits*

Need at least 5 credits for document generation.
Current balance: {credit_details.get('balance', 0)} credits

Buy top-ups: T10, T50, T100, T500""")
                        continue
                    
                    send_whatsapp(from_number, f"""📄 *Document Generation*

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
                            send_whatsapp(from_number, "❌ Document generation requires active subscription.")
                            continue
                        
                        cost = doc_costs[doc_num]
                        credit_details = get_credit_details(canonical_account_id)
                        if int(credit_details.get("balance", 0)) < cost:
                            send_whatsapp(from_number, f"❌ Need {cost} credits. Balance: {credit_details.get('balance', 0)}")
                            continue
                        
                        success, message = deduct_credits(canonical_account_id, cost, f"Document: {doc_names[doc_num]}")
                        if success:
                            doc_ref = f"DOC_{doc_names[doc_num].replace(' ', '_')}_{uuid.uuid4().hex[:8]}"
                            send_whatsapp(from_number, f"""📄 *Document Generated*

📋 Type: {doc_names[doc_num]}
🆔 Reference: {doc_ref}
💳 Credits Used: {cost}

{DISCLAIMER_DOC}

Reply 8 for main menu""")
                        else:
                            send_whatsapp(from_number, f"❌ {message}")
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
                        send_whatsapp(from_number, "📋 *Filing History*\n\nNo filings found.\n\nStart a filing with 7 then F1.")
                    else:
                        history = "📋 *Filing History*\n\n"
                        for filing in history_result.data[:5]:
                            history += f"• {filing.get('tax_type', 'Unknown')}: {filing.get('filing_reference', 'N/A')}\n  📅 {filing.get('submitted_at', '')[:10]} | 💳 {filing.get('credits_used', 0)} credits\n\n"
                        history += f"\n{DISCLAIMER_FILING}"
                        send_whatsapp(from_number, history)
                    continue
                
                if text.upper() == 'F0':
                    send_whatsapp(from_number, get_main_menu())
                    continue
                
                # ============ OPTION 6 - BUY CREDITS MENU ============
                if text == '6':
                    if not has_active_subscription(canonical_account_id):
                        send_whatsapp(from_number, "❌ Active subscription required for top-ups. Reply 4 to view plans.")
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
                        send_whatsapp(from_number, f"""📋 *YOUR SUBSCRIPTION*

✅ ACTIVE
📅 Expires: {expires_at}
📊 Balance: {credit_details.get('balance', 0)} credits
• Top-up: {credit_details.get('topup_credits', 0)}
• Plan: {credit_details.get('plan_credits', 0)}

{DISCLAIMER_SUBSCRIPTION}""")
                    else:
                        send_whatsapp(from_number, f"""📋 *NO ACTIVE SUBSCRIPTION*

Free Plan:
• Database answers: 50/day
• Tax calculations: unlimited (use CALC command)

Reply 4 to view plans

{DISCLAIMER_MAIN}""")
                    continue
                
                # ============ OPTION 2 - CHECK BALANCE ============
                if text == '2':
                    credit_details = get_credit_details(canonical_account_id)
                    send_whatsapp(from_number, f"""💎 *Credit Balance*

Total: *{credit_details.get('balance', 0)}* credits
• Top-up: {credit_details.get('topup_credits', 0)} (used first)
• Plan: {credit_details.get('plan_credits', 0)}

{DISCLAIMER_CREDITS}""")
                    continue
                
                # ============ OPTION 1 - ASK QUESTION ============
                if text == '1':
                    user_state[from_number] = {"step": "asking_question", "timestamp": current_time}
                    send_whatsapp(from_number, "💬 Please type your tax question.\n\n# - Menu | 0 - Cancel")
                    continue
                
                # ============ OPTION 5 - PREMIUM FEATURES ============
                if text == '5':
                    send_whatsapp(from_number, f"""🔗 *Premium Features*

✨ With active subscription:
• AI answers (1 credit)
• PAYE filing (10 credits)
• VAT filing (15 credits)
• CIT filing (20 credits)
• Document generation (5-10 credits)

{DISCLAIMER_MAIN}""")
                    continue
                
                # ============ HANDLE ASKING QUESTION STATE ============
                if from_number in user_state and user_state[from_number].get("step") == "asking_question":
                    if SERVICES_AVAILABLE:
                        if not has_active_subscription(canonical_account_id):
                            balance = get_credit_balance(canonical_account_id)
                            if balance <= 0:
                                send_whatsapp(from_number, f"❌ AI answers require active subscription or credits.\n\nBuy top-ups: T10, T50, T100, T500\nSubscribe: Reply 4")
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
                            send_whatsapp(from_number, f"{answer}\n\n---\n💎 *Credits remaining:* {new_balance}\n\n{DISCLAIMER_AI}\n\nReply 1 for another question or 8 for menu.")
                        else:
                            send_whatsapp(from_number, f"❌ {result.get('error', 'Unknown error')}\n\n{DISCLAIMER_AI}")
                    else:
                        send_whatsapp(from_number, "❌ AI service unavailable.")
                    user_state.pop(from_number, None)
                    continue
                
                # ============ DEFAULT - SEND MAIN MENU ============
                send_whatsapp(from_number, get_main_menu())
        
        return "ok"
    except Exception as e:
        logging.error(f"Error in webhook: {e}")
        return "error", 500

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8000))
    app.run(host='0.0.0.0', port=port)