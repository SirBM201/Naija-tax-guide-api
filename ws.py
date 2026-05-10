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

def get_plans_list_menu():
    try:
        result = supabase.table("plans").select("*").eq("active", True).execute()
        plans = result.data or []
        
        if not plans:
            return "📋 *Subscription Plans*\n\nNo plans available at the moment. Please check back later."
        
        menu_lines = ["📋 *AVAILABLE SUBSCRIPTION PLANS*\n"]
        
        # Separate plans by tier
        starter_plans = [p for p in plans if "starter" in p.get("plan_code", "")]
        professional_plans = [p for p in plans if "professional" in p.get("plan_code", "")]
        business_plans = [p for p in plans if "business" in p.get("plan_code", "")]
        
        def get_billing(plan_code):
            if "yearly" in plan_code:
                return "year"
            elif "quarterly" in plan_code:
                return "quarter"
            return "month"
        
        def sort_by_billing(plan_list):
            order = {"monthly": 0, "quarterly": 1, "yearly": 2}
            return sorted(plan_list, key=lambda x: order[get_billing(x.get("plan_code", ""))])
        
        if starter_plans:
            menu_lines.append("*STARTER PLANS*")
            for plan in sort_by_billing(starter_plans):
                name = plan.get("name", "Unknown")
                price = plan.get("price", 0)
                credits = plan.get("ai_credits_total", 0)
                billing = get_billing(plan.get("plan_code", ""))
                menu_lines.append(f"  • {name} - ₦{price:,}/{billing} - {credits} credits")
            menu_lines.append("")
        
        if professional_plans:
            menu_lines.append("*PROFESSIONAL PLANS*")
            for plan in sort_by_billing(professional_plans):
                name = plan.get("name", "Unknown")
                price = plan.get("price", 0)
                credits = plan.get("ai_credits_total", 0)
                billing = get_billing(plan.get("plan_code", ""))
                menu_lines.append(f"  • {name} - ₦{price:,}/{billing} - {credits} credits")
            menu_lines.append("")
        
        if business_plans:
            menu_lines.append("*BUSINESS PLANS*")
            for plan in sort_by_billing(business_plans):
                name = plan.get("name", "Unknown")
                price = plan.get("price", 0)
                credits = plan.get("ai_credits_total", 0)
                billing = get_billing(plan.get("plan_code", ""))
                menu_lines.append(f"  • {name} - ₦{price:,}/{billing} - {credits} credits")
            menu_lines.append("")
        
        menu_lines.append("💡 *How to subscribe:*")
        menu_lines.append("Reply with the plan name (e.g., 'Starter Monthly')")
        menu_lines.append("Or reply with the code:")
        menu_lines.append("  • S1 - Starter Monthly")
        menu_lines.append("  • S2 - Starter Quarterly")
        menu_lines.append("  • S3 - Starter Yearly")
        menu_lines.append("  • P1 - Professional Monthly")
        menu_lines.append("  • P2 - Professional Quarterly")
        menu_lines.append("  • P3 - Professional Yearly")
        menu_lines.append("  • B1 - Business Monthly")
        menu_lines.append("  • B2 - Business Quarterly")
        menu_lines.append("  • B3 - Business Yearly")
        menu_lines.append("\n0 - Cancel | # - Main Menu")
        
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
                        send_whatsapp(from_number, "Send a valid number")
                else:
                    send_whatsapp(from_number, get_main_menu())
        
        return "ok"
    except Exception as e:
        logging.error(f"Error: {e}")
        return "error", 500

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8000))
    app.run(host='0.0.0.0', port=port)