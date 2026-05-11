import os
import re
import logging
import uuid
import random
import string
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template_string
import requests
from dotenv import load_dotenv
from supabase import create_client, Client
from collections import defaultdict
import time

load_dotenv()

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# ============ SUPABASE ============
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = None

if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    logging.info("✅ Supabase connected")

# ============ WHATSAPP ============
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "naija-tax-guide-verify")
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
WHATSAPP_API_URL = "https://graph.facebook.com/v18.0"

# ============ PAYSTACK ============
PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY")
PAYSTACK_API_URL = "https://api.paystack.co"

# Credit packages with LETTER CODES (to avoid menu number conflicts)
CREDIT_PACKAGES = {
    "T10": {"credits": 10, "amount_ngn": 500, "amount_kobo": 50000, "code": "T10", "description": "10 AI Credits"},
    "T50": {"credits": 50, "amount_ngn": 2000, "amount_kobo": 200000, "code": "T50", "description": "50 AI Credits"},
    "T100": {"credits": 100, "amount_ngn": 3500, "amount_kobo": 350000, "code": "T100", "description": "100 AI Credits"},
    "T500": {"credits": 500, "amount_ngn": 15000, "amount_kobo": 1500000, "code": "T500", "description": "500 AI Credits"},
}

# Track user state
user_state = {}
user_cooldown = defaultdict(float)

# ============ CREDIT FUNCTIONS ============
def get_or_create_account_id(phone_number):
    """Get or create account_id for a WhatsApp user"""
    try:
        # First try to find existing user
        user_result = supabase.table("bot_users").select("auth_user_id").eq("platform", "whatsapp").eq("user_id", str(phone_number)).execute()
        
        if user_result.data and user_result.data[0].get("auth_user_id"):
            account_id = user_result.data[0].get("auth_user_id")
            logging.info(f"Found existing user: {account_id}")
            return account_id
        
        # Create new user
        auth_user_id = str(uuid.uuid4())
        
        # Insert user
        supabase.table("bot_users").insert({
            "platform": "whatsapp",
            "user_id": str(phone_number),
            "auth_user_id": auth_user_id,
            "created_at": datetime.now().isoformat(),
            "total_calculations": 0,
            "is_active": True
        }).execute()
        
        # Insert credit balance
        supabase.table("ai_credit_balances").insert({
            "account_id": auth_user_id,
            "balance": 0,
            "updated_at": datetime.now().isoformat()
        }).execute()
        
        logging.info(f"Created new user with account_id: {auth_user_id}")
        return auth_user_id
    except Exception as e:
        logging.error(f"Error creating account: {e}")
        return None

def get_credit_balance(account_id):
    """Get current credit balance"""
    try:
        result = supabase.table("ai_credit_balances").select("balance").eq("account_id", account_id).limit(1).execute()
        if result.data:
            return int(result.data[0].get("balance", 0))
        return 0
    except Exception as e:
        logging.error(f"Error getting balance: {e}")
        return 0

def add_credits_topup(account_id, credits, reference):
    """ADD credits to existing balance (TOP-UP, not replace) - Fixed type conversion"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            # First, check if balance record exists
            existing = supabase.table("ai_credit_balances").select("balance").eq("account_id", account_id).execute()
            
            if existing.data:
                # Convert to int - database might return string
                current_balance = int(existing.data[0].get("balance", 0))
                new_balance = current_balance + int(credits)  # Ensure credits is also int
                
                # Update existing balance
                supabase.table("ai_credit_balances").update({
                    "balance": new_balance,
                    "updated_at": datetime.now().isoformat()
                }).eq("account_id", account_id).execute()
                
                logging.info(f"✅ Top-up: Added {credits} credits to {account_id}. Old: {current_balance}, New: {new_balance}")
                return True
            else:
                # Create new balance record
                supabase.table("ai_credit_balances").insert({
                    "account_id": account_id,
                    "balance": int(credits),
                    "updated_at": datetime.now().isoformat()
                }).execute()
                
                logging.info(f"✅ Top-up: Created new balance with {credits} credits for {account_id}")
                return True
                
        except Exception as e:
            logging.error(f"Attempt {attempt + 1} failed to add credits: {e}")
            if attempt < max_retries - 1:
                time.sleep(1)
            else:
                logging.error(f"All attempts failed to add credits for {account_id}")
                return False

def get_credit_packages_menu():
    return """💎 *Buy AI Credits*

Reply with the package code AFTER pressing 6:

T10 - 10 credits - ₦500
T50 - 50 credits - ₦2,000
T100 - 100 credits - ₦3,500
T500 - 500 credits - ₦15,000

0 - Cancel | # - Main Menu"""

def create_credit_payment(account_id, package_code, phone_number):
    """Create Paystack payment for credit purchase"""
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
        result = supabase.table("plans").select("*").eq("active", True).execute()
        return result.data or []
    except Exception as e:
        logging.error(f"Error fetching plans: {e}")
        return []

def get_user_subscription(phone_number):
    try:
        user_result = supabase.table("bot_users").select("auth_user_id").eq("platform", "whatsapp").eq("user_id", str(phone_number)).execute()
        if not user_result.data:
            return None
        
        auth_user_id = user_result.data[0].get("auth_user_id")
        if not auth_user_id:
            return None
        
        sub_result = supabase.table("subscriptions").select("*").eq("account_id", auth_user_id).eq("status", "active").order("created_at", desc=True).limit(1).execute()
        
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
🎯 0 AI credits (Unlimited database answers)

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
            return "📋 *Subscription Plans*\n\nNo plans available at the moment. Please check back later."
        
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
    """Handle Paystack webhook - Fixed with type conversion"""
    try:
        payload = request.get_json()
        if not payload:
            logging.warning("No payload received")
            return "No payload", 400
        
        event = payload.get('event')
        data = payload.get('data', {})
        
        logging.info(f"📨 Billing webhook received: {event}")
        
        if event == 'charge.success':
            metadata = data.get('metadata', {})
            transaction_type = metadata.get('type', 'subscription')
            
            if transaction_type == 'credit_purchase':
                account_id = metadata.get('account_id')
                credits = metadata.get('credits', 0)
                reference = data.get('reference')
                phone_number = metadata.get('provider_user_id')
                amount = data.get('amount', 0) / 100
                
                logging.info(f"💰 Credit purchase: account={account_id}, credits={credits}, phone={phone_number}, ref={reference}")
                
                if account_id and credits:
                    # Add credits to existing balance
                    success = add_credits_topup(account_id, credits, reference)
                    
                    if success and phone_number:
                        # Get updated balance
                        time.sleep(1)  # Give database time to update
                        new_balance = get_credit_balance(account_id)
                        old_balance = new_balance - credits
                        
                        # Send detailed WhatsApp confirmation
                        confirmation_msg = f"""✅ *CREDITS ADDED SUCCESSFULLY!*

💎 *{credits} AI credits* have been ADDED to your account.

💰 Amount paid: ₦{amount:,.2f}
🆔 Reference: {reference}
📊 Previous balance: {old_balance} credits
✨ New balance: *{new_balance} credits*

💡 Each credit = 1 AI tax question

Reply 1 to ask a tax question or 8 for main menu."""
                        
                        send_whatsapp(phone_number, confirmation_msg)
                        logging.info(f"✅ Top-up completed and notification sent to {phone_number}: +{credits} credits")
                    else:
                        logging.error(f"❌ Failed to add credits for {account_id}")
                        
                        # Send error notification
                        if phone_number:
                            send_whatsapp(phone_number, 
                                "⚠️ *Payment received but credit addition failed!*\n\n"
                                "Our team has been notified and will add your credits shortly.\n\n"
                                "Please reply with 6 to check your balance or contact support.\n\n"
                                f"Reference: {reference}")
                else:
                    logging.error(f"Missing account_id or credits in webhook")
            
            elif transaction_type == 'subscription':
                phone_number = metadata.get('phone')
                plan_name = metadata.get('plan_name', 'Subscription')
                reference = data.get('reference')
                amount = data.get('amount', 0) / 100
                plan_code = metadata.get('plan_code')
                
                logging.info(f"📋 Subscription purchase: phone={phone_number}, plan={plan_name}, ref={reference}")
                
                if phone_number:
                    confirmation_msg = f"""✅ *PAYMENT SUCCESSFUL!*

🎉 Thank you for your subscription!

📋 Plan: {plan_name}
💰 Amount: ₦{amount:,.2f}
🆔 Reference: {reference}

Your subscription is now ACTIVE.

Reply 8 for main menu."""
                    
                    send_whatsapp(phone_number, confirmation_msg)
                    
                    # Update subscription in database
                    try:
                        user_result = supabase.table("bot_users").select("auth_user_id").eq("platform", "whatsapp").eq("user_id", str(phone_number)).execute()
                        if user_result.data:
                            auth_user_id = user_result.data[0].get("auth_user_id")
                            if auth_user_id:
                                # Check if subscription already exists
                                existing_sub = supabase.table("subscriptions").select("*").eq("paystack_ref", reference).execute()
                                
                                if not existing_sub.data:
                                    supabase.table("subscriptions").insert({
                                        "account_id": auth_user_id,
                                        "user_id": auth_user_id,
                                        "plan_code": plan_code,
                                        "plan": plan_code,
                                        "status": "active",
                                        "paystack_ref": reference,
                                        "amount": float(amount),
                                        "amount_kobo": int(amount * 100),
                                        "currency": "NGN",
                                        "created_at": datetime.now().isoformat(),
                                        "updated_at": datetime.now().isoformat()
                                    }).execute()
                                    logging.info(f"✅ Subscription activated for {phone_number}: {plan_name}")
                                else:
                                    logging.info(f"Subscription already exists for reference: {reference}")
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
                
                # Check for duplicate message (2 second cooldown)
                current_time = datetime.now().timestamp()
                cooldown_key = f"{from_number}:{text}"
                if user_cooldown[cooldown_key] > current_time - 2:
                    logging.info(f"Skipping duplicate message: {text}")
                    continue
                user_cooldown[cooldown_key] = current_time
                
                # Auto-clear stale state (5 minutes inactivity)
                if from_number in user_state:
                    state_time = user_state[from_number].get("timestamp", 0)
                    if current_time - state_time > 300:
                        user_state.pop(from_number, None)
                        logging.info(f"Cleared stale state for {from_number}")
                
                logging.info(f"Message from {from_number}: {text}")
                
                # Get or create account_id
                account_id = get_or_create_account_id(from_number)
                if not account_id:
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
                    if from_number in user_state:
                        state = user_state[from_number]
                        step = state.get("step", 0)
                        if step == 2:
                            user_state.pop(from_number, None)
                            send_whatsapp(from_number, get_plans_list_menu())
                        else:
                            user_state.pop(from_number, None)
                            send_whatsapp(from_number, get_main_menu())
                    else:
                        send_whatsapp(from_number, get_main_menu())
                    continue
                
                # ============ OPTION 5: LINK WEBSITE ACCOUNT (Placeholder) ============
                if text == '5':
                    send_whatsapp(from_number, """🔗 *Link to Website Account*

This feature will be available soon.

To link your account, please visit:
www.naijataxguides.com/channels

Reply 8 for main menu.""")
                    continue
                
                # ============ OPTION 6: BUY AI CREDITS ============
                if text == '6':
                    user_state[from_number] = {"step": "buy_credits", "timestamp": current_time}
                    send_whatsapp(from_number, get_credit_packages_menu())
                    continue
                
                # Handle credit package selection (using LETTER CODES: T10, T50, T100, T500)
                if from_number in user_state and user_state[from_number].get("step") == "buy_credits":
                    package_code = text.upper().strip()
                    
                    if package_code in ["T10", "T50", "T100", "T500"]:
                        package = CREDIT_PACKAGES.get(package_code)
                        
                        if package:
                            payment = create_credit_payment(account_id, package_code, from_number)
                            if payment and payment.get("success"):
                                send_whatsapp(from_number, f"""💎 *Payment Link Generated!*

Package: {package['description']}
Amount: ₦{package['amount_ngn']:,}

🔗 *Click here to complete payment:*
{payment['payment_link']}

After successful payment, your credits will be ADDED to your existing balance.

💡 Reference: {payment['reference']}

0 - Cancel | # - Main Menu""")
                                user_state.pop(from_number, None)
                            else:
                                send_whatsapp(from_number, "❌ Failed to generate payment link. Please try again later.\n\nReply 8 for main menu.")
                                user_state.pop(from_number, None)
                        else:
                            send_whatsapp(from_number, "❌ Invalid package. Please reply with T10, T50, T100, or T500.")
                    elif text == '0':
                        user_state.pop(from_number, None)
                        send_whatsapp(from_number, "❌ Cancelled.\n\nReply 8 for main menu.")
                    else:
                        send_whatsapp(from_number, "Please reply with T10, T50, T100, or T500 to select a package, or 0 to cancel.")
                    continue
                
                # Handle ambiguous selection response
                if from_number in user_state and user_state[from_number].get("ambiguous"):
                    state = user_state[from_number]
                    matches = state.get("matches", [])
                    
                    if text in ["1", "2"]:
                        idx = int(text) - 1
                        if idx < len(matches):
                            plan = matches[idx]
                            user_state[from_number] = {"step": 2, "plan": plan, "timestamp": datetime.now().timestamp()}
                            welcome_msg = f"""✅ *Plan Selected:* {plan.get('name')}

💰 Price: ₦{plan.get('price', 0):,}
🎯 Credits: {plan.get('ai_credits_total', 0)} AI credits

📧 *Please provide your email address* to receive the payment link.

Email example: name@example.com

* - Back | 0 - Cancel | # - Main Menu"""
                            send_whatsapp(from_number, welcome_msg)
                        else:
                            send_whatsapp(from_number, "Invalid selection. Please reply with 1 or 2, or 0 to cancel.")
                    elif text == '0':
                        user_state.pop(from_number, None)
                        send_whatsapp(from_number, "❌ Cancelled.\n\nReply 8 for main menu.")
                    else:
                        send_whatsapp(from_number, "Please reply with 1 or 2 to select your plan, or 0 to cancel.")
                    continue
                
                # Handle email collection (step 2)
                if from_number in user_state and user_state[from_number].get("step") == 2:
                    plan = user_state[from_number].get("plan")
                    email = text.strip().lower()
                    
                    # Email validation pattern
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
                                    user_state[from_number] = {"step": 3, "plan": plan, "reference": reference, "timestamp": datetime.now().timestamp()}
                                    
                                    response_msg = f"""✅ *Payment Link Generated!*

Plan: {plan.get('name')}
Amount: ₦{plan.get('price', 0):,}

🔗 *Click here to complete payment:*
{payment_link}

After successful payment, you will be redirected back to WhatsApp.

💡 Payment reference: {reference}

0 - Cancel | # - Main Menu"""
                                    send_whatsapp(from_number, response_msg)
                                else:
                                    send_whatsapp(from_number, "❌ Failed to generate payment link. Please try again later.\n\nReply 4 to view plans again.")
                                    user_state.pop(from_number, None)
                            else:
                                send_whatsapp(from_number, "❌ Payment service error. Please try again later.")
                                user_state.pop(from_number, None)
                        except Exception as e:
                            logging.error(f"Payment error: {e}")
                            send_whatsapp(from_number, "❌ Failed to generate payment link. Please try again later.")
                            user_state.pop(from_number, None)
                    elif text == '0' or text == '#':
                        user_state.pop(from_number, None)
                        if text == '#':
                            send_whatsapp(from_number, get_main_menu())
                        else:
                            send_whatsapp(from_number, "❌ Cancelled.\n\nReply 8 for main menu.")
                    else:
                        send_whatsapp(from_number, "❌ *Invalid email address.*\n\nPlease send a valid email address (e.g., name@example.com).\n\n* - Back | 0 - Cancel | # - Main Menu")
                    continue
                
                # Main menu navigation
                if text == '4':
                    plans = get_plans_list_menu()
                    send_whatsapp(from_number, plans)
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
                    message = format_subscription_message(subscription, plan)
                    send_whatsapp(from_number, message)
                elif text == '1':
                    send_whatsapp(from_number, "💬 Please type your tax question.\n\n💡 # - Save & Menu | 0 - Cancel")
                elif text == '2':
                    balance = get_credit_balance(account_id)
                    if balance == 0:
                        send_whatsapp(from_number, f"💎 *AI Credits Balance*\n\nYou have *0 credits* remaining.\n\nEach credit = 1 AI tax question.\n\nTo buy credits:\n1. Press 6 to see packages\n2. Then reply with T10, T50, T100, or T500")
                    else:
                        send_whatsapp(from_number, f"💎 *AI Credits Balance*\n\nYou have *{balance} credits* remaining.\n\nEach credit = 1 AI tax question.\n\nTo buy more credits:\n1. Press 6 to see packages\n2. Then reply with T10, T50, T100, or T500")
                elif text == '7':
                    send_whatsapp(from_number, """*📋 TAX FILING & MANAGEMENT*

This feature is coming soon. Stay tuned!""")
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
                    # Check if user typed a T code without pressing 6 first
                    if text.upper().strip() in ["T10", "T50", "T100", "T500"]:
                        send_whatsapp(from_number, "💡 *To buy credits:* Press 6 first to see packages, then reply with T10, T50, T100, or T500.\n\nReply 8 for main menu.")
                        continue
                    
                    plans = get_all_plans()
                    result = find_plan_by_input(plans, text)
                    
                    if result.get("found"):
                        if result.get("ambiguous"):
                            matches = result.get("matches", [])
                            match_type = result.get("type", "criteria")
                            
                            selection_msg = f"🔍 *Multiple plans found with this {match_type}:*\n\n"
                            for i, plan in enumerate(matches, 1):
                                name = plan.get("name", "Unknown")
                                price = plan.get("price", 0)
                                credits = plan.get("ai_credits_total", 0)
                                plan_code = plan.get("plan_code", "")
                                
                                billing = "month"
                                if "quarterly" in plan_code:
                                    billing = "quarter"
                                elif "yearly" in plan_code:
                                    billing = "year"
                                
                                selection_msg += f"{i}. {name} - ₦{price:,}/{billing} - {credits} credits\n"
                            
                            selection_msg += "\nPlease reply with the number (1 or 2) to select your plan.\n0 - Cancel | # - Main Menu"
                            
                            send_whatsapp(from_number, selection_msg)
                            user_state[from_number] = {"ambiguous": True, "matches": matches, "timestamp": datetime.now().timestamp()}
                        else:
                            plan = result.get("plan")
                            user_state[from_number] = {"step": 2, "plan": plan, "timestamp": datetime.now().timestamp()}
                            
                            welcome_msg = f"""✅ *Plan Selected:* {plan.get('name')}

💰 Price: ₦{plan.get('price', 0):,}
🎯 Credits: {plan.get('ai_credits_total', 0)} AI credits

📧 *Please provide your email address* to receive the payment link.

Email example: name@example.com

* - Back | 0 - Cancel | # - Main Menu"""
                            send_whatsapp(from_number, welcome_msg)
                    else:
                        send_whatsapp(from_number, get_main_menu())
        
        return "ok"
    except Exception as e:
        logging.error(f"Error in webhook: {e}")
        return "error", 500

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8000))
    app.run(host='0.0.0.0', port=port)