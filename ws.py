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

# ============ SUPABASE - DUAL CLIENT SETUP WITH RLS ============
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_KEY") or os.getenv("SUPABASE_ANON_KEY")
# Try multiple possible environment variable names for service role key
SUPABASE_SERVICE_ROLE_KEY = (
    os.getenv("SUPABASE_SERVICE_ROLE_KEY") or 
    os.getenv("SUPABASE_SERVICE_KEY") or 
    os.getenv("SERVICE_ROLE_KEY") or
    os.getenv("SUPABASE_ADMIN_KEY")
)

# Client for authenticated users (respects RLS)
supabase: Client = None

# Admin client for backend operations (bypasses RLS via service role)
supabase_admin: Client = None

if SUPABASE_URL and SUPABASE_ANON_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    logging.info("✅ Supabase client connected (respects RLS)")

if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
    supabase_admin = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    logging.info("✅ Supabase admin client connected (bypasses RLS for backend ops)")
else:
    logging.error("❌ SUPABASE_SERVICE_ROLE_KEY not set. Admin operations will fail!")
    logging.error(f"Available keys: SUPABASE_KEY={bool(SUPABASE_ANON_KEY)}, SERVICE_ROLE_KEY={bool(SUPABASE_SERVICE_ROLE_KEY)}")

# ============ IMPORT SERVICES ============
try:
    from app.services.ask_service import ask_guarded
    from app.services.credits_service import get_credit_balance as get_credits_balance
    SERVICES_AVAILABLE = True
    logging.info("✅ Services imported successfully")
except Exception as e:
    logging.error(f"❌ Failed to import services: {e}")
    SERVICES_AVAILABLE = False

# ============ WHATSAPP ============
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "naija-tax-guide-verify")
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
WHATSAPP_API_URL = "https://graph.facebook.com/v18.0"

# ============ PAYSTACK ============
PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY")
PAYSTACK_API_URL = "https://api.paystack.co"

# Credit packages with LETTER CODES
CREDIT_PACKAGES = {
    "T10": {"credits": 10, "amount_ngn": 500, "amount_kobo": 50000, "code": "T10", "description": "10 AI Credits"},
    "T50": {"credits": 50, "amount_ngn": 2000, "amount_kobo": 200000, "code": "T50", "description": "50 AI Credits"},
    "T100": {"credits": 100, "amount_ngn": 3500, "amount_kobo": 350000, "code": "T100", "description": "100 AI Credits"},
    "T500": {"credits": 500, "amount_ngn": 15000, "amount_kobo": 1500000, "code": "T500", "description": "500 AI Credits"},
}

# Track user state
user_state = {}
user_cooldown = defaultdict(float)

# ============ DATABASE OPERATION HELPERS ============

def get_admin_client():
    """Get admin client for backend writes (bypasses RLS)"""
    if not supabase_admin:
        logging.error("Supabase admin client not available. Check SUPABASE_SERVICE_ROLE_KEY environment variable.")
        raise Exception("Supabase admin client not available. Set SUPABASE_SERVICE_ROLE_KEY.")
    return supabase_admin

def get_read_client():
    """Get read client for queries (respects RLS)"""
    if not supabase:
        raise Exception("Supabase client not available.")
    return supabase

# ============ ACCOUNT MANAGEMENT ============

def get_canonical_account_id(phone_number):
    """
    Get or create canonical account_id that works with ask_guarded service.
    Uses admin client for ALL writes (bypasses RLS) since this is backend operation.
    """
    try:
        read_client = get_read_client()
        
        # Step 1: Check if user exists in bot_users (using read client)
        user_result = read_client.table("bot_users").select("auth_user_id").eq("platform", "whatsapp").eq("user_id", str(phone_number)).execute()
        
        if user_result.data and user_result.data[0].get("auth_user_id"):
            auth_user_id = user_result.data[0].get("auth_user_id")
            logging.info(f"Found existing bot_user: {auth_user_id}")
            
            # Step 2: Check if account exists in accounts table
            account_result = read_client.table("accounts").select("account_id").eq("account_id", auth_user_id).execute()
            
            if not account_result.data:
                # Create accounts entry using admin client
                logging.info(f"Creating accounts entry for existing user {auth_user_id}")
                admin_client = get_admin_client()
                admin_client.table("accounts").insert({
                    "account_id": auth_user_id,
                    "id": auth_user_id,
                    "provider": "whatsapp",
                    "provider_user_id": str(phone_number),
                    "phone": str(phone_number),
                    "created_at": datetime.now().isoformat(),
                    "updated_at": datetime.now().isoformat(),
                    "has_used_trial": False
                }).execute()
                logging.info(f"✅ Created accounts entry for {auth_user_id}")
            
            return auth_user_id
        
        # Step 3: Create new user - MUST use admin client for ALL inserts
        auth_user_id = str(uuid.uuid4())
        logging.info(f"Creating new user with ID: {auth_user_id}")
        
        # Get admin client (will raise exception if not available)
        admin_client = get_admin_client()
        
        # Insert into bot_users using ADMIN CLIENT
        admin_client.table("bot_users").insert({
            "platform": "whatsapp",
            "user_id": str(phone_number),
            "auth_user_id": auth_user_id,
            "created_at": datetime.now().isoformat(),
            "total_calculations": 0,
            "is_active": True
        }).execute()
        logging.info(f"✅ Created bot_users entry")
        
        # Insert into accounts using ADMIN CLIENT
        admin_client.table("accounts").insert({
            "account_id": auth_user_id,
            "id": auth_user_id,
            "provider": "whatsapp",
            "provider_user_id": str(phone_number),
            "phone": str(phone_number),
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "has_used_trial": False
        }).execute()
        logging.info(f"✅ Created accounts entry")
        
        # Insert credit balance using ADMIN CLIENT
        admin_client.table("ai_credit_balances").insert({
            "account_id": auth_user_id,
            "balance": 0,
            "plan_credits": 0,
            "topup_credits": 0,
            "updated_at": datetime.now().isoformat()
        }).execute()
        logging.info(f"✅ Created credit balance")
        
        logging.info(f"✅ New user fully created: {auth_user_id}")
        return auth_user_id
        
    except Exception as e:
        logging.error(f"Error getting canonical account: {e}")
        return None

def get_credit_balance(account_id):
    """Get current credit balance using read client"""
    try:
        if SERVICES_AVAILABLE:
            return get_credits_balance(account_id)
        else:
            read_client = get_read_client()
            result = read_client.table("ai_credit_balances").select("balance").eq("account_id", account_id).limit(1).execute()
            if result.data:
                return int(result.data[0].get("balance", 0))
            return 0
    except Exception as e:
        logging.error(f"Error getting balance: {e}")
        return 0

def get_credit_details(account_id):
    """Get detailed credit information"""
    try:
        read_client = get_read_client()
        result = read_client.table("ai_credit_balances").select("*").eq("account_id", account_id).limit(1).execute()
        if result.data:
            return result.data[0]
        return {"balance": 0, "plan_credits": 0, "topup_credits": 0}
    except Exception as e:
        logging.error(f"Error getting credit details: {e}")
        return {"balance": 0, "plan_credits": 0, "topup_credits": 0}

def add_topup_credits(account_id, credits, reference):
    """Add top-up credits to user's balance using admin client for write"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            admin_client = get_admin_client()
            
            # Check existing balance
            existing = admin_client.table("ai_credit_balances").select("*").eq("account_id", account_id).execute()
            
            if existing.data:
                current_balance = int(existing.data[0].get("balance", 0))
                current_topup = int(existing.data[0].get("topup_credits", 0))
                
                new_topup = current_topup + int(credits)
                new_balance = current_balance + int(credits)
                
                admin_client.table("ai_credit_balances").update({
                    "balance": new_balance,
                    "topup_credits": new_topup,
                    "updated_at": datetime.now().isoformat()
                }).eq("account_id", account_id).execute()
                
                logging.info(f"✅ Top-up: +{credits} credits. Old: {current_balance}, New: {new_balance}")
                return True
            else:
                admin_client.table("ai_credit_balances").insert({
                    "account_id": account_id,
                    "balance": int(credits),
                    "plan_credits": 0,
                    "topup_credits": int(credits),
                    "updated_at": datetime.now().isoformat()
                }).execute()
                
                logging.info(f"✅ Top-up: Created new balance with {credits} credits")
                return True
                
        except Exception as e:
            logging.error(f"Attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(1)
            else:
                logging.error(f"All attempts failed")
                return False

def get_credit_packages_menu():
    return """💎 *Buy AI Credits*

Reply with the package code AFTER pressing 6:

T10 - 10 credits - ₦500
T50 - 50 credits - ₦2,000
T100 - 100 credits - ₦3,500
T500 - 500 credits - ₦15,000

0 - Cancel | # - Main Menu"""

def check_pending_transaction(account_id):
    """Check if there's a pending transaction for this user"""
    try:
        read_client = get_read_client()
        five_min_ago = (datetime.now() - timedelta(minutes=5)).isoformat()
        pending = read_client.table("paystack_transactions")\
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

def create_credit_payment(account_id, package_code, phone_number):
    """Create Paystack payment for credit purchase"""
    existing_reference = check_pending_transaction(account_id)
    if existing_reference:
        admin_client = get_admin_client()
        admin_client.table("paystack_transactions").update({
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
            if data.get("status"):
                admin_client = get_admin_client()
                admin_client.table("paystack_transactions").insert({
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

# ============ TAX CALCULATION ============
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

def get_all_plans():
    try:
        read_client = get_read_client()
        result = read_client.table("plans").select("*").eq("active", True).execute()
        return result.data or []
    except Exception as e:
        logging.error(f"Error fetching plans: {e}")
        return []

def get_user_subscription(phone_number):
    try:
        canonical_account_id = get_canonical_account_id(phone_number)
        if not canonical_account_id:
            return None
        
        read_client = get_read_client()
        sub_result = read_client.table("subscriptions").select("*").eq("account_id", canonical_account_id).eq("status", "active").order("created_at", desc=True).limit(1).execute()
        
        if sub_result.data:
            return sub_result.data[0]
        return None
    except Exception as e:
        logging.error(f"Error getting subscription: {e}")
        return None

def format_subscription_message(subscription, plan):
    if not subscription:
        return """📋 *NO ACTIVE SUBSCRIPTION*

You are on the Free Plan.

💰 Free plan: ₦0
🎯 0 AI credits

Reply with 4 to view available plans and upgrade."""
    
    plan_code = subscription.get("plan_code", "Unknown")
    amount = subscription.get("amount", 0)
    created_at = subscription.get("created_at", "")
    status = subscription.get("status", "active")
    
    plan_credits = plan.get("ai_credits_total", 0) if plan else 0
    plan_display = plan.get("name", plan_code) if plan else plan_code
    
    created_date = created_at[:10] if created_at else "Unknown"
    
    return f"""📋 *YOUR SUBSCRIPTION*

✅ Plan: {plan_display}
💰 Amount: ₦{amount:,.2f}
🎯 Credits: {plan_credits} AI credits
📅 Activated: {created_date}
📊 Status: {status.upper()}

✨ You have {plan_credits} AI credits available.
🔄 Your subscription auto-renews.

To cancel or upgrade, contact support."""

def extract_number(text):
    cleaned = text.replace('₦', '').replace(',', '').replace(' ', '').strip()
    match = re.search(r'(\d+)', cleaned)
    if match:
        return int(match.group(1))
    return None

def find_plans_by_credits(plans, credits):
    return [p for p in plans if p.get("ai_credits_total", 0) == credits]

def find_plans_by_price(plans, price):
    return [p for p in plans if p.get("price", 0) == price]

def find_plan_by_code(plans, code):
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
    
    target_code = code_map.get(code.upper())
    if target_code:
        for plan in plans:
            if plan.get("plan_code", "") == target_code:
                return plan
    return None

def find_plan_by_name(plans, name):
    name_lower = name.lower().strip()
    for plan in plans:
        if plan.get("name", "").lower() == name_lower:
            return plan
    return None

def find_plan_by_input(plans, user_input):
    user_input = user_input.strip()
    
    plan = find_plan_by_code(plans, user_input)
    if plan:
        return {"found": True, "plan": plan, "ambiguous": False}
    
    plan = find_plan_by_name(plans, user_input)
    if plan:
        return {"found": True, "plan": plan, "ambiguous": False}
    
    number = extract_number(user_input)
    if number is not None:
        price_matches = find_plans_by_price(plans, number)
        if len(price_matches) == 1:
            return {"found": True, "plan": price_matches[0], "ambiguous": False}
        elif len(price_matches) > 1:
            return {"found": True, "ambiguous": True, "matches": price_matches, "type": "price"}
        
        credits_matches = find_plans_by_credits(plans, number)
        if len(credits_matches) == 1:
            return {"found": True, "plan": credits_matches[0], "ambiguous": False}
        elif len(credits_matches) > 1:
            return {"found": True, "ambiguous": True, "matches": credits_matches, "type": "credits"}
    
    return {"found": False}

def get_plans_list_menu():
    try:
        plans = get_all_plans()
        
        if not plans:
            return "📋 *Subscription Plans*\n\nNo plans available at the moment."
        
        menu_lines = ["📋 *AVAILABLE SUBSCRIPTION PLANS*\n"]
        
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
                menu_lines.append(f"  • *{name}* - ₦{price:,}/{billing_display} - {credits} credits")
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
                menu_lines.append(f"  • *{name}* - ₦{price:,}/{billing_display} - {credits} credits")
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
                menu_lines.append(f"  • *{name}* - ₦{price:,}/{billing_display} - {credits} credits")
                menu_lines.append(f"    (Code: {short_code})")
            menu_lines.append("")
        
        menu_lines.append("💡 *How to subscribe:*")
        menu_lines.append("")
        menu_lines.append("1️⃣ *By Plan Name:* Type the full plan name")
        menu_lines.append("2️⃣ *By Code:* Type the short code (e.g., S1, P2, B3)")
        menu_lines.append("3️⃣ *By Credits:* Type the number of AI credits")
        menu_lines.append("4️⃣ *By Price:* Type the amount (e.g., 5000 or ₦5,000)")
        menu_lines.append("")
        menu_lines.append("0 - Cancel | # - Main Menu")
        
        return "\n".join(menu_lines)
    except Exception as e:
        logging.error(f"Error fetching plans: {e}")
        return "📋 *Subscription Plans*\n\nPlease visit www.naijataxguides.com/plans"

def get_main_menu():
    return """*🤖 Naija Tax Guide*

Reply with:

1️⃣ - Ask a tax question
2️⃣ - Check AI credits balance
3️⃣ - Check my subscription plan
4️⃣ - View subscription plans
5️⃣ - Link to website account (coming soon)
6️⃣ - Buy AI credits
7️⃣ - Tax filing & management (coming soon)
8️⃣ - Help / Menu

---
*Global commands:*
# - Save & Menu | * - Back | 0 - Cancel | 9 - Resume

*Calculator:*
Type 'calc paye 500000' to calculate tax"""

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
            
            # Check if already processed
            try:
                read_client = get_read_client()
                existing_tx = read_client.table("paystack_transactions").select("status").eq("reference", reference).execute()
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
                    success = add_topup_credits(account_id, credits, reference)
                    
                    if success and phone_number:
                        try:
                            admin_client = get_admin_client()
                            admin_client.table("paystack_transactions").update({
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

💎 *{credits} AI credits* added to your account.

💰 Amount: ₦{amount:,.2f}
🆔 Reference: {reference}

📊 *Current Balance:*
• Total: *{total_balance}* credits
• Top-up: {topup_credits} (used first)
• Plan: {plan_credits}

Reply 1 for tax questions or 8 for menu."""
                        
                        send_whatsapp(phone_number, confirmation_msg)
                        logging.info(f"✅ Top-up completed: +{credits} credits")
                    else:
                        logging.error(f"❌ Failed to add credits for {account_id}")
                        if phone_number:
                            send_whatsapp(phone_number, f"⚠️ Payment received but credit addition failed. Reference: {reference}\n\nReply 8 for menu.")
            
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

Your subscription is now ACTIVE.

Reply 8 for menu."""
                    
                    send_whatsapp(phone_number, confirmation_msg)
                    
                    try:
                        canonical_account_id = get_canonical_account_id(phone_number)
                        if canonical_account_id:
                            plan_details = None
                            for p in get_all_plans():
                                if p.get("plan_code") == plan_code:
                                    plan_details = p
                                    break
                            
                            plan_credits = plan_details.get("ai_credits_total", 0) if plan_details else 0
                            duration_days = plan_details.get("duration_days", 30) if plan_details else 30
                            expires_at = datetime.now() + timedelta(days=duration_days)
                            
                            admin_client = get_admin_client()
                            admin_client.table("subscriptions").insert({
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
                            
                            # Update credit balance
                            existing_balance = admin_client.table("ai_credit_balances").select("*").eq("account_id", canonical_account_id).execute()
                            if existing_balance.data:
                                current_topup = int(existing_balance.data[0].get("topup_credits", 0))
                                new_balance = plan_credits + current_topup
                                admin_client.table("ai_credit_balances").update({
                                    "balance": new_balance,
                                    "plan_credits": plan_credits,
                                    "subscription_expires_at": expires_at.isoformat(),
                                    "updated_at": datetime.now().isoformat()
                                }).eq("account_id", canonical_account_id).execute()
                            else:
                                admin_client.table("ai_credit_balances").insert({
                                    "account_id": canonical_account_id,
                                    "balance": plan_credits,
                                    "plan_credits": plan_credits,
                                    "topup_credits": 0,
                                    "subscription_expires_at": expires_at.isoformat(),
                                    "updated_at": datetime.now().isoformat()
                                }).execute()
                            
                            logging.info(f"✅ Subscription activated: {plan_name}")
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
                
                # Get canonical account_id
                canonical_account_id = get_canonical_account_id(from_number)
                if not canonical_account_id:
                    send_whatsapp(from_number, "❌ Error initializing your account. Please try again later.")
                    continue
                
                # Global commands
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
                
                # Option 5 - Link website account
                if text == '5':
                    send_whatsapp(from_number, "🔗 *Link to Website Account*\n\nFeature coming soon.\n\nReply 8 for main menu.")
                    continue
                
                # Option 6 - Buy AI credits
                if text == '6':
                    user_state[from_number] = {"step": "buy_credits", "timestamp": current_time}
                    send_whatsapp(from_number, get_credit_packages_menu())
                    continue
                
                # Handle credit package selection
                if from_number in user_state and user_state[from_number].get("step") == "buy_credits":
                    package_code = text.upper().strip()
                    
                    if package_code in ["T10", "T50", "T100", "T500"]:
                        package = CREDIT_PACKAGES.get(package_code)
                        if package:
                            payment = create_credit_payment(canonical_account_id, package_code, from_number)
                            if payment and payment.get("success"):
                                send_whatsapp(from_number, f"""💎 *Payment Link Generated!*

Package: {package['description']}
Amount: ₦{package['amount_ngn']:,}

🔗 {payment['payment_link']}

Reference: {payment['reference']}

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
                
                # Handle subscription plan selection
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
                
                # Handle tax question (when in asking state after pressing 1)
                if from_number in user_state and user_state[from_number].get("step") == "asking_question":
                    if SERVICES_AVAILABLE:
                        balance = get_credit_balance(canonical_account_id)
                        if balance <= 0:
                            send_whatsapp(from_number, "❌ You have 0 credits.\n\nBuy credits: Press 6\nSubscribe: Press 4")
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
                            send_whatsapp(from_number, f"{answer}\n\n---\n💎 Remaining credits: {new_balance}\n\nReply 1 for another question or 8 for menu.")
                        else:
                            error = result.get("error", "Unknown error")
                            if error == "insufficient_credits":
                                send_whatsapp(from_number, "❌ Insufficient credits.\n\nBuy credits: Press 6\nSubscribe: Press 4")
                            else:
                                send_whatsapp(from_number, f"❌ Error: {error}\n\nPlease try again.")
                    else:
                        send_whatsapp(from_number, "❌ AI service unavailable. Please try again later.")
                    
                    user_state.pop(from_number, None)
                    continue
                
                # Handle direct question (no state, just type a question)
                is_question = (len(text) > 10 and not text.upper().startswith('T') and not text.isdigit() and text not in ['#', '*', '0', '1', '2', '3', '4', '5', '6', '7', '8', '9'])
                
                if is_question and from_number not in user_state:
                    if SERVICES_AVAILABLE:
                        balance = get_credit_balance(canonical_account_id)
                        if balance <= 0:
                            send_whatsapp(from_number, "❌ You have 0 credits.\n\nBuy credits: Press 6\nSubscribe: Press 4")
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
                            send_whatsapp(from_number, f"{answer}\n\n---\n💎 Remaining credits: {new_balance}\n\nReply 1 for another question or 8 for menu.")
                        else:
                            error = result.get("error", "Unknown error")
                            if error == "insufficient_credits":
                                send_whatsapp(from_number, "❌ Insufficient credits.\n\nBuy credits: Press 6\nSubscribe: Press 4")
                            else:
                                send_whatsapp(from_number, f"❌ Error: {error}\n\nPlease try again.")
                    else:
                        send_whatsapp(from_number, "❌ AI service unavailable.")
                    continue
                
                # Main menu navigation
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
                    send_whatsapp(from_number, format_subscription_message(subscription, plan))
                elif text == '1':
                    user_state[from_number] = {"step": "asking_question", "timestamp": current_time}
                    send_whatsapp(from_number, "💬 Please type your tax question.\n\n💡 # - Menu | 0 - Cancel")
                elif text == '2':
                    balance = get_credit_balance(canonical_account_id)
                    credit_details = get_credit_details(canonical_account_id)
                    topup_credits = credit_details.get("topup_credits", 0)
                    plan_credits = credit_details.get("plan_credits", 0)
                    send_whatsapp(from_number, f"""💎 *AI Credits Balance*

Total: *{balance}* credits
• Top-up: {topup_credits} (used first)
• Plan: {plan_credits}

Each credit = 1 AI tax question.

Buy credits: Press 6""")
                elif text == '7':
                    send_whatsapp(from_number, "📋 *TAX FILING & MANAGEMENT*\n\nComing soon!")
                elif text.isdigit() and len(text) >= 5:
                    try:
                        salary = float(text.replace(',', ''))
                        data = calculate_paye(salary)
                        result = f"""*PAYE RESULT*

Gross: ₦{data['gross']:,.0f}
Pension: ₦{data['pension']:,.0f}
NHF: ₦{data['nhf']:,.0f}
Tax: ₦{data['tax']:,.0f}
Net: *₦{data['net']:,.0f}*
Rate: {data['rate']}%"""
                        send_whatsapp(from_number, result)
                    except:
                        send_whatsapp(from_number, "Send a valid number (e.g., 500000)")
                else:
                    # Check for T codes without pressing 6
                    if text.upper().strip() in ["T10", "T50", "T100", "T500"]:
                        send_whatsapp(from_number, "💡 To buy credits: Press 6 first, then reply with T10, T50, T100, or T500.\n\nReply 8 for menu.")
                        continue
                    
                    # Check if user is selecting a plan
                    if from_number in user_state and user_state[from_number].get("step") == "selecting_plan":
                        plans = get_all_plans()
                        result = find_plan_by_input(plans, text)
                        
                        if result.get("found") and not result.get("ambiguous"):
                            plan = result.get("plan")
                            user_state[from_number] = {"step": 2, "plan": plan, "timestamp": datetime.now().timestamp()}
                            send_whatsapp(from_number, f"""✅ *Plan Selected:* {plan.get('name')}

💰 Price: ₦{plan.get('price', 0):,}
🎯 Credits: {plan.get('ai_credits_total', 0)} AI credits

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

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8000))
    app.run(host='0.0.0.0', port=port)