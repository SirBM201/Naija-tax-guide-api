import os
import re
import logging
import uuid
from datetime import datetime
from flask import Flask, request, jsonify, render_template_string
import requests
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = None

if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    logging.info("✅ Supabase connected")

WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "naija-tax-guide-verify")
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
WHATSAPP_API_URL = "https://graph.facebook.com/v18.0"

PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY")
PAYSTACK_API_URL = "https://api.paystack.co"

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

def get_user_subscription(phone_number):
    """Get user's active subscription using phone number directly"""
    try:
        # Query subscriptions by phone number stored in metadata
        sub_result = supabase.table("subscriptions").select("*").eq("status", "active").execute()
        
        if sub_result.data:
            for sub in sub_result.data:
                if sub.get("paystack_ref"):
                    # Check if this subscription belongs to this phone
                    # For now, return the most recent
                    return sub_result.data[0] if sub_result.data else None
        return None
    except Exception as e:
        logging.error(f"Error getting subscription: {e}")
        return None

def format_subscription_message(subscription, plan):
    if not subscription:
        return """📋 *NO ACTIVE SUBSCRIPTION*

You are on the Free Plan.

Reply with 4 to view available plans and upgrade."""
    
    plan_code = subscription.get("plan_code", "Unknown")
    amount = subscription.get("amount", 0)
    created_at = subscription.get("created_at", "")
    
    plan_display = plan.get("name", plan_code) if plan else plan_code
    
    return f"""📋 *YOUR SUBSCRIPTION*

✅ Plan: {plan_display}
💰 Amount: ₦{amount:,.2f}
📅 Activated: {created_at[:10] if created_at else "Unknown"}
📊 Status: ACTIVE

Reply 8 for main menu."""

def extract_number(text):
    cleaned = text.replace('₦', '').replace(',', '').replace(' ', '').strip()
    match = re.search(r'(\d+)', cleaned)
    if match:
        return int(match.group(1))
    return None

def find_plan_by_input(plans, user_input):
    user_input = user_input.strip().lower()
    
    # Check by name
    for plan in plans:
        if plan.get("name", "").lower() == user_input:
            return {"found": True, "plan": plan}
    
    # Check by code
    code_map = {"s1": "starter_monthly", "s2": "starter_quarterly", "s3": "starter_yearly",
                "p1": "professional_monthly", "p2": "professional_quarterly", "p3": "professional_yearly",
                "b1": "business_monthly", "b2": "business_quarterly", "b3": "business_yearly"}
    
    if user_input in code_map:
        target = code_map[user_input]
        for plan in plans:
            if plan.get("plan_code", "") == target:
                return {"found": True, "plan": plan}
    
    # Check by number (credits or price)
    number = extract_number(user_input)
    if number:
        for plan in plans:
            if plan.get("price", 0) == number or plan.get("ai_credits_total", 0) == number:
                return {"found": True, "plan": plan}
    
    return {"found": False}

def create_paystack_payment(plan, email, phone_number, reference):
    try:
        amount = plan.get("price", 0) * 100
        plan_name = plan.get("name", "Subscription")
        plan_code = plan.get("plan_code", "")
        
        base_url = os.getenv("PUBLIC_BACKEND_BASE_URL", "https://incredible-nonie-bmsconcept-37359733.koyeb.app")
        callback_url = f"{base_url}/payment/success?phone={phone_number}&plan={plan_name}"
        
        payload = {
            "amount": amount,
            "email": email,
            "reference": reference,
            "currency": "NGN",
            "metadata": {
                "plan_code": plan_code,
                "plan_name": plan_name,
                "phone": phone_number,
                "channel": "whatsapp"
            },
            "callback_url": callback_url
        }
        
        headers = {"Authorization": f"Bearer {PAYSTACK_SECRET_KEY}", "Content-Type": "application/json"}
        response = requests.post(f"{PAYSTACK_API_URL}/transaction/initialize", json=payload, headers=headers, timeout=30)
        
        if response.status_code == 200:
            data = response.json()
            if data.get("status"):
                return {"success": True, "payment_link": data["data"]["authorization_url"], "reference": reference}
        
        return {"success": False, "error": "Payment initialization failed"}
    except Exception as e:
        logging.error(f"Paystack error: {e}")
        return {"success": False, "error": str(e)}

def get_plans_list_menu():
    try:
        plans = get_all_plans()
        if not plans:
            return "📋 *Subscription Plans*\n\nNo plans available."
        
        menu_lines = ["📋 *AVAILABLE SUBSCRIPTION PLANS*\n"]
        
        for plan in plans:
            name = plan.get("name", "Unknown")
            price = plan.get("price", 0)
            credits = plan.get("ai_credits_total", 0)
            plan_code = plan.get("plan_code", "")
            
            if "monthly" in plan_code:
                billing = "month"
            elif "quarterly" in plan_code:
                billing = "quarter"
            else:
                billing = "year"
            
            menu_lines.append(f"• *{name}* - ₦{price:,}/{billing} - {credits} credits")
        
        menu_lines.append("\n💡 Reply with the plan name, code (S1, P1, B1), or credits amount")
        menu_lines.append("0 - Cancel | # - Main Menu")
        
        return "\n".join(menu_lines)
    except Exception as e:
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
0 - Cancel | # - Main Menu"""

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

SUCCESS_PAGE = """<!DOCTYPE html>
<html>
<head><title>Payment Successful</title></head>
<body style="text-align:center;padding:50px;font-family:Arial">
<h1>✅ Payment Successful!</h1>
<p>Your subscription is now active.</p>
<p>Redirecting to WhatsApp...</p>
<script>setTimeout(function(){ window.location.href = "{{ whatsapp_url }}"; }, 3000);</script>
</body>
</html>"""

@app.route('/health', methods=['GET'])
def health():
    return "OK"

@app.route('/payment/success', methods=['GET'])
def payment_success():
    phone = request.args.get('phone', '')
    plan_name = request.args.get('plan', 'Subscription')
    clean_phone = re.sub(r'\D', '', phone)
    if len(clean_phone) == 13 and clean_phone.startswith('234'):
        clean_phone = clean_phone[3:]
    whatsapp_url = f"https://wa.me/{clean_phone}"
    return render_template_string(SUCCESS_PAGE, whatsapp_url=whatsapp_url)

@app.route('/api/billing/webhook', methods=['POST'])
def billing_webhook():
    try:
        payload = request.get_json()
        if not payload:
            return "No payload", 400
        
        event = payload.get('event')
        data = payload.get('data', {})
        
        if event == 'charge.success':
            metadata = data.get('metadata', {})
            phone_number = metadata.get('phone')
            plan_name = metadata.get('plan_name', 'Subscription')
            reference = data.get('reference')
            amount = data.get('amount', 0) / 100
            plan_code = metadata.get('plan_code')
            
            if phone_number:
                # Insert subscription directly
                try:
                    supabase.table("subscriptions").insert({
                        "plan_code": plan_code,
                        "status": "active",
                        "paystack_ref": reference,
                        "amount": amount,
                        "amount_kobo": amount * 100,
                        "currency": "NGN",
                        "created_at": datetime.now().isoformat()
                    }).execute()
                    logging.info(f"Subscription activated: {plan_name}")
                    
                    # Send confirmation
                    send_whatsapp(phone_number, f"✅ *PAYMENT SUCCESSFUL!*\n\nPlan: {plan_name}\nAmount: ₦{amount:,.2f}\n\nYour subscription is ACTIVE!")
                except Exception as e:
                    logging.error(f"Insert error: {e}")
        
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
                logging.info(f"Message from {from_number}: {text}")
                
                if text == '#':
                    user_state.pop(from_number, None)
                    send_whatsapp(from_number, get_main_menu())
                    continue
                
                if text == '0':
                    user_state.pop(from_number, None)
                    send_whatsapp(from_number, "❌ Cancelled.\n\nReply 8 for main menu.")
                    continue
                
                if text == '8':
                    send_whatsapp(from_number, get_main_menu())
                elif text == '4':
                    send_whatsapp(from_number, get_plans_list_menu())
                elif text == '3':
                    subscription = get_user_subscription(from_number)
                    plan = None
                    if subscription:
                        plans = get_all_plans()
                        for p in plans:
                            if p.get("plan_code") == subscription.get("plan_code"):
                                plan = p
                                break
                    send_whatsapp(from_number, format_subscription_message(subscription, plan))
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
                    # Try to find plan
                    plans = get_all_plans()
                    result = find_plan_by_input(plans, text)
                    
                    if result.get("found"):
                        plan = result.get("plan")
                        # Ask for email
                        user_state[from_number] = {"step": 2, "plan": plan}
                        send_whatsapp(from_number, f"✅ *Plan Selected:* {plan.get('name')}\n\n💰 Price: ₦{plan.get('price', 0):,}\n🎯 Credits: {plan.get('ai_credits_total', 0)}\n\n📧 *Please provide your email address* to receive payment link.\n\n0 - Cancel | # - Main Menu")
                    else:
                        send_whatsapp(from_number, get_main_menu())
                
                # Handle email input (step 2)
                if from_number in user_state and user_state[from_number].get("step") == 2:
                    plan = user_state[from_number].get("plan")
                    email = text.strip().lower()
                    
                    if "@" in email and "." in email:
                        reference = f"SUB_{plan.get('plan_code')}_{uuid.uuid4().hex[:8]}"
                        payment = create_paystack_payment(plan, email, from_number, reference)
                        
                        if payment.get("success"):
                            send_whatsapp(from_number, f"✅ *Payment Link Generated!*\n\nPlan: {plan.get('name')}\nAmount: ₦{plan.get('price', 0):,}\n\n🔗 {payment.get('payment_link')}\n\nAfter payment, you'll be redirected back to WhatsApp.\n\n0 - Cancel | # - Main Menu")
                            user_state.pop(from_number, None)
                        else:
                            send_whatsapp(from_number, "❌ Failed to generate payment link. Try again.\n\nReply 4 to view plans.")
                            user_state.pop(from_number, None)
                    else:
                        send_whatsapp(from_number, "❌ *Invalid email.* Send a valid email address.")
        
        return "ok"
    except Exception as e:
        logging.error(f"Error: {e}")
        return "error", 500

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8000))
    app.run(host='0.0.0.0', port=port)