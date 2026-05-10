import os
import re
import logging
import uuid
from datetime import datetime
from flask import Flask, request, jsonify
import requests
from dotenv import load_dotenv
from supabase import create_client, Client

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
PAYSTACK_PUBLIC_KEY = os.getenv("PAYSTACK_PUBLIC_KEY")
PAYSTACK_API_URL = "https://api.paystack.co"

# Track user state for subscription flow
user_state = {}

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

def extract_number(text):
    """Extract digits from text, handling ₦ and commas"""
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
    
    # 1. Check by Plan Code
    plan = find_plan_by_code(plans, user_input)
    if plan:
        return {"found": True, "plan": plan, "ambiguous": False}
    
    # 2. Check by Plan Name
    plan = find_plan_by_name(plans, user_input)
    if plan:
        return {"found": True, "plan": plan, "ambiguous": False}
    
    # Extract number for price/credits search
    number = extract_number(user_input)
    if number is not None:
        # 3. Check by Price
        price_matches = find_plans_by_price(plans, number)
        if len(price_matches) == 1:
            return {"found": True, "plan": price_matches[0], "ambiguous": False}
        elif len(price_matches) > 1:
            return {"found": True, "ambiguous": True, "matches": price_matches, "type": "price"}
        
        # 4. Check by Credits
        credits_matches = find_plans_by_credits(plans, number)
        if len(credits_matches) == 1:
            return {"found": True, "plan": credits_matches[0], "ambiguous": False}
        elif len(credits_matches) > 1:
            return {"found": True, "ambiguous": True, "matches": credits_matches, "type": "credits"}
    
    return {"found": False}

def create_paystack_payment(plan, email, phone_number, reference):
    """Create Paystack payment link"""
    try:
        amount = plan.get("price", 0) * 100  # Convert to kobo
        plan_name = plan.get("name", "Subscription")
        
        # Get callback URL from environment or use default
        base_url = os.getenv("PUBLIC_BACKEND_BASE_URL", "https://incredible-nonie-bmsconcept-37359733.koyeb.app")
        callback_url = f"{base_url}/api/paystack/callback"
        
        payload = {
            "amount": amount,
            "email": email,
            "reference": reference,
            "currency": "NGN",
            "metadata": {
                "plan_code": plan.get("plan_code"),
                "plan_name": plan_name,
                "phone": phone_number,
                "channel": "whatsapp"
            },
            "callback_url": callback_url
        }
        
        headers = {
            "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
            "Content-Type": "application/json"
        }
        
        response = requests.post(f"{PAYSTACK_API_URL}/transaction/initialize", json=payload, headers=headers, timeout=30)
        
        if response.status_code == 200:
            data = response.json()
            if data.get("status"):
                return {"success": True, "payment_link": data["data"]["authorization_url"], "reference": reference}
        
        logging.error(f"Paystack error: {response.text}")
        return {"success": False, "error": "Payment initialization failed"}
    except Exception as e:
        logging.error(f"Paystack error: {e}")
        return {"success": False, "error": str(e)}

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
5️⃣ - Link to website account
6️⃣ - Buy AI credits
7️⃣ - Tax filing & management
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
        return False
    except Exception as e:
        logging.error(f"Send error: {e}")
        return False

@app.route('/health', methods=['GET'])
def health():
    return "OK"

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
                logging.info(f"Message from {from_number}: {text}")
                
                # Handle global commands
                if text == '#':
                    user_state.pop(from_number, None)
                    send_whatsapp(from_number, get_main_menu())
                    continue
                
                if text == '0':
                    user_state.pop(from_number, None)
                    send_whatsapp(from_number, "❌ Subscription cancelled.\n\nReply 8 for main menu.")
                    continue
                
                if text == '*':
                    # Go back one step
                    if from_number in user_state:
                        state = user_state[from_number]
                        step = state.get("step", 0)
                        if step == 2:  # Going back from email entry to plan selection
                            user_state.pop(from_number, None)
                            send_whatsapp(from_number, get_plans_list_menu())
                        else:
                            user_state.pop(from_number, None)
                            send_whatsapp(from_number, get_main_menu())
                    else:
                        send_whatsapp(from_number, get_main_menu())
                    continue
                
                # Handle ambiguous selection response
                if from_number in user_state and user_state[from_number].get("ambiguous"):
                    state = user_state[from_number]
                    matches = state.get("matches", [])
                    
                    if text in ["1", "2"]:
                        idx = int(text) - 1
                        if idx < len(matches):
                            plan = matches[idx]
                            # Store selected plan and move to email collection
                            user_state[from_number] = {"step": 2, "plan": plan}
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
                        send_whatsapp(from_number, "❌ Subscription cancelled.\n\nReply 8 for main menu.")
                    else:
                        send_whatsapp(from_number, "Please reply with 1 or 2 to select your plan, or 0 to cancel.")
                    continue
                
                # Handle email collection (step 2)
                if from_number in user_state and user_state[from_number].get("step") == 2:
                    plan = user_state[from_number].get("plan")
                    email = text.strip().lower()
                    
                    # Validate email
                    if "@" in email and "." in email and len(email) > 5:
                        # Generate unique reference
                        reference = f"SUB_{plan.get('plan_code')}_{uuid.uuid4().hex[:8]}"
                        
                        # Create Paystack payment
                        payment = create_paystack_payment(plan, email, from_number, reference)
                        
                        if payment.get("success"):
                            payment_link = payment.get("payment_link")
                            
                            # Store payment reference in user state
                            user_state[from_number] = {"step": 3, "plan": plan, "reference": reference}
                            
                            response = f"""✅ *Payment Link Generated!*

Plan: {plan.get('name')}
Amount: ₦{plan.get('price', 0):,}

🔗 *Click here to complete payment:*
{payment_link}

After successful payment, your subscription will be activated automatically.

💡 Payment reference: {reference}

0 - Cancel | # - Main Menu"""
                            send_whatsapp(from_number, response)
                        else:
                            send_whatsapp(from_number, f"❌ Failed to generate payment link. Please try again later.\n\nReply 4 to view plans again.")
                            user_state.pop(from_number, None)
                    else:
                        send_whatsapp(from_number, "❌ *Invalid email address.*\n\nPlease send a valid email address (e.g., name@example.com).\n\n* - Back | 0 - Cancel | # - Main Menu")
                    continue
                
                # Main menu navigation
                if text == '4':
                    plans = get_plans_list_menu()
                    send_whatsapp(from_number, plans)
                elif text == '8':
                    send_whatsapp(from_number, get_main_menu())
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
                    # Try to find plan by input
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
                            user_state[from_number] = {"ambiguous": True, "matches": matches}
                        else:
                            plan = result.get("plan")
                            # Store selected plan and move to email collection
                            user_state[from_number] = {"step": 2, "plan": plan}
                            
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
        logging.error(f"Error: {e}")
        return "error", 500

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8000))
    app.run(host='0.0.0.0', port=port)