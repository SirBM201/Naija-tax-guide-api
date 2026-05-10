import os
import re
import logging
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
    """Extract digits from text"""
    match = re.search(r'[\d,]+', text.replace(',', ''))
    if match:
        return float(match.group())
    return None

def find_plan_by_input(plans, user_input):
    user_input = user_input.strip()
    input_lower = user_input.lower()
    
    # Code mapping
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
    
    # Check by Plan Code FIRST (S1, P1, B1, etc.)
    if input_lower in code_map:
        target_code = code_map[input_lower]
        for plan in plans:
            if plan.get("plan_code", "") == target_code:
                return plan
    
    # Check by Plan Name
    for plan in plans:
        if plan.get("name", "").lower() == input_lower:
            return plan
        # Also check partial matches for common variations
        if "starter" in input_lower and "starter" in plan.get("plan_code", ""):
            if "monthly" in input_lower and "monthly" in plan.get("plan_code", ""):
                return plan
            if "quarterly" in input_lower and "quarterly" in plan.get("plan_code", ""):
                return plan
            if "yearly" in input_lower and "yearly" in plan.get("plan_code", ""):
                return plan
        if "professional" in input_lower and "professional" in plan.get("plan_code", ""):
            if "monthly" in input_lower and "monthly" in plan.get("plan_code", ""):
                return plan
            if "quarterly" in input_lower and "quarterly" in plan.get("plan_code", ""):
                return plan
            if "yearly" in input_lower and "yearly" in plan.get("plan_code", ""):
                return plan
        if "business" in input_lower and "business" in plan.get("plan_code", ""):
            if "monthly" in input_lower and "monthly" in plan.get("plan_code", ""):
                return plan
            if "quarterly" in input_lower and "quarterly" in plan.get("plan_code", ""):
                return plan
            if "yearly" in input_lower and "yearly" in plan.get("plan_code", ""):
                return plan
    
    # Extract number for credits or price search
    number = extract_number(user_input)
    if number is not None:
        # Check if user included "credits" keyword
        if "credit" in input_lower:
            # Search by credits
            for plan in plans:
                if plan.get("ai_credits_total", 0) == number:
                    return plan
        else:
            # Search by price
            for plan in plans:
                if plan.get("price", 0) == number:
                    return plan
    
    return None

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
        
        # Code mapping for display
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
        
        menu_lines.append("💡 *How to subscribe (4 ways):*")
        menu_lines.append("")
        menu_lines.append("1️⃣ *By Plan Name:*")
        menu_lines.append("   Type the full plan name")
        menu_lines.append("   Example: `Starter Monthly`")
        menu_lines.append("")
        menu_lines.append("2️⃣ *By Code:*")
        menu_lines.append("   Type the short code shown above")
        menu_lines.append("   Example: `S1` for Starter Monthly")
        menu_lines.append("")
        menu_lines.append("3️⃣ *By Credits:*")
        menu_lines.append("   Type `100 credits` or just `100`")
        menu_lines.append("   Example: `100 credits` finds Starter Monthly")
        menu_lines.append("")
        menu_lines.append("4️⃣ *By Price:*")
        menu_lines.append("   Type `₦14,000` or `14000`")
        menu_lines.append("   Example: `14000` finds Starter Quarterly")
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
Type 'calc paye 500000' to calculate tax

---
Reply LANGUAGE or L to change language"""

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
                    # Try to find plan by input (name, code, credits, price)
                    plans = get_all_plans()
                    plan = find_plan_by_input(plans, text)
                    
                    if plan:
                        plan_name = plan.get("name", "Unknown")
                        price = plan.get("price", 0)
                        credits = plan.get("ai_credits_total", 0)
                        plan_code = plan.get("plan_code", "")
                        
                        billing = "month"
                        if "quarterly" in plan_code:
                            billing = "quarter"
                        elif "yearly" in plan_code:
                            billing = "year"
                        
                        response = f"""*SUBSCRIPTION SELECTED*

✅ Plan: {plan_name}
💰 Price: ₦{price:,}/{billing}
🎯 AI Credits: {credits} credits per {billing}

To complete your subscription, please visit:
www.naijataxguides.com/subscribe

Or reply with 'CONFIRM' to proceed via WhatsApp.

0 - Cancel | # - Main Menu"""
                        send_whatsapp(from_number, response)
                    else:
                        send_whatsapp(from_number, get_main_menu())
        
        return "ok"
    except Exception as e:
        logging.error(f"Error: {e}")
        return "error", 500

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8000))
    app.run(host='0.0.0.0', port=port)