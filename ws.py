import os
import re
import logging
import random
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

def calculate_cit(turnover):
    profit = turnover * 0.20
    if turnover < 25000000:
        rate = 0
        size = "Small (Exempt)"
    elif turnover <= 100000000:
        rate = 20
        size = "Medium"
    else:
        rate = 30
        size = "Large"
    cit = profit * rate / 100
    education = profit * 0.03
    total = cit + education
    return {"turnover": turnover, "profit": profit, "size": size, "total": round(total)}

def calculate_vat(amount, inclusive=False):
    if inclusive:
        vat = amount * 0.075 / 1.075
        exclusive = amount - vat
        total = amount
    else:
        vat = amount * 0.075
        exclusive = amount
        total = amount + vat
    return {"amount": amount, "vat": round(vat), "exclusive": round(exclusive), "total": round(total)}

WHT_RATES = {"consultancy": 10, "rent": 10, "interest": 10, "dividend": 10, "construction": 5, "contracts": 5, "transport": 3}

def calculate_wht(amount, trans_type):
    rate = WHT_RATES.get(trans_type, 10)
    wht = amount * rate / 100
    return {"amount": amount, "rate": rate, "wht": round(wht), "net": round(amount - wht)}

# ============ SUBSCRIPTION PLANS ============
def get_plans_list_menu():
    try:
        result = supabase.table("plans").select("*").eq("active", True).execute()
        plans = result.data or []
        
        if not plans:
            return "📋 *Subscription Plans*\n\nNo plans available at the moment. Please check back later."
        
        plans.sort(key=lambda x: x.get("price", 0))
        
        menu_lines = ["📋 *Subscription Plans*\n"]
        
        # Add Free Plan
        menu_lines.append("*Free Plan* - ₦0/month")
        menu_lines.append("• 5 AI questions per month")
        menu_lines.append("• Basic tax calculator")
        menu_lines.append("• Standard support\n")
        
        # Add Starter (Pro) plans
        starter_plans = [p for p in plans if "starter" in p.get("plan_code", "")]
        if starter_plans:
            menu_lines.append("*Pro Plan* - ₦5,000/month")
            menu_lines.append("• 50 AI questions per month")
            menu_lines.append("• Advanced calculator")
            menu_lines.append("• Priority support")
            menu_lines.append("• Export reports\n")
        
        # Add Business plans
        business_plans = [p for p in plans if "business" in p.get("plan_code", "")]
        if business_plans:
            menu_lines.append("*Business Plan* - ₦15,000/month")
            menu_lines.append("• Unlimited AI questions")
            menu_lines.append("• All features")
            menu_lines.append("• API access")
            menu_lines.append("• Dedicated support\n")
        
        menu_lines.append("Reply with:")
        menu_lines.append("1️⃣ - Upgrade to Pro")
        menu_lines.append("2️⃣ - Upgrade to Business")
        menu_lines.append("3️⃣ - Back to menu")
        
        return "\n".join(menu_lines)
    except Exception as e:
        logging.error(f"Error: {e}")
        return "📋 *Subscription Plans*\n\nPlease visit www.naijataxguides.com/plans"

# ============ MENUS ============
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

def get_tax_menu():
    return """*📋 TAX FILING & MANAGEMENT*

Select an option:

1️⃣ - PAYE Tax Calculator
2️⃣ - Company Income Tax (CIT)
3️⃣ - VAT Calculator
4️⃣ - Withholding Tax (WHT)
5️⃣ - Salary Comparison
6️⃣ - Tax Quiz
7️⃣ - Tax Calendar & Deadlines
8️⃣ - Filing Guides & Checklists
9️⃣ - Back to Main Menu

---
Send # to save and return to main menu"""

def get_calculator_menu():
    return """*🧮 TAX CALCULATOR*

Enter your calculation:

*PAYE:* `calc paye 500000`
*CIT:* `calc cit 50000000`
*VAT:* `calc vat 100000`
*VAT inclusive:* `calc vatin 107500`
*WHT:* `calc wht 500000 consultancy`

Or select a calculator:
1️⃣ - PAYE
2️⃣ - CIT
3️⃣ - VAT
4️⃣ - WHT
5️⃣ - Back"""

def get_help_menu():
    return """*❓ Help - How to Use This Bot*

*Main Menu:*
• Send 1-8 to navigate the menu
• Send # to save and return to main menu
• Send * to go back
• Send 0 to cancel
• Send 9 to resume

*Quick Commands:*
• `calc paye 500000` - Calculate PAYE
• `calc cit 50000000` - Calculate CIT
• `calc vat 100000` - Calculate VAT
• `calc wht 500000 consultancy` - Calculate WHT
• `LANGUAGE` - Change language

*Support:*
• Send a question directly for AI tax assistance
• Or select Option 1 from main menu"""

# ============ SEND MESSAGE ============
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

# ============ WEBHOOK ============
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
                
                # Handle quick calc command
                calc_match = re.match(r'^calc\s+(paye|cit|vat|vatin|wht)\s+([\d,]+)(?:\s+(\w+))?', text.lower())
                if calc_match:
                    calc_type = calc_match.group(1)
                    amount = float(calc_match.group(2).replace(',', ''))
                    
                    if calc_type == 'paye':
                        data = calculate_paye(amount)
                        result = f"""*PAYE SUMMARY*

Gross: ₦{data['gross']:,.0f}
Pension: ₦{data['pension']:,.0f}
NHF: ₦{data['nhf']:,.0f}
Tax: ₦{data['tax']:,.0f}
Net: *₦{data['net']:,.0f}*
Rate: {data['rate']}%"""
                        send_whatsapp(from_number, result)
                    elif calc_type == 'cit':
                        data = calculate_cit(amount)
                        result = f"""*CIT SUMMARY*

Turnover: ₦{data['turnover']:,.0f}
Profit: ₦{data['profit']:,.0f}
Size: {data['size']}
Tax: *₦{data['total']:,.0f}*"""
                        send_whatsapp(from_number, result)
                    elif calc_type == 'vat':
                        data = calculate_vat(amount, False)
                        result = f"""*VAT (7.5%)*

Amount (excl): ₦{data['amount']:,.0f}
VAT: ₦{data['vat']:,.0f}
Total: ₦{data['total']:,.0f}"""
                        send_whatsapp(from_number, result)
                    elif calc_type == 'vatin':
                        data = calculate_vat(amount, True)
                        result = f"""*VAT (7.5%)*

Amount (incl): ₦{data['amount']:,.0f}
VAT: ₦{data['vat']:,.0f}
Exclusive: ₦{data['exclusive']:,.0f}"""
                        send_whatsapp(from_number, result)
                    elif calc_type == 'wht':
                        trans_type = calc_match.group(3) if calc_match.group(3) else "consultancy"
                        data = calculate_wht(amount, trans_type)
                        result = f"""*WITHHOLDING TAX*

Amount: ₦{data['amount']:,.0f}
Rate: {data['rate']}%
WHT: *₦{data['wht']:,.0f}*
Net: ₦{data['net']:,.0f}"""
                        send_whatsapp(from_number, result)
                    continue
                
                # Menu navigation
                if text == '1':
                    send_whatsapp(from_number, "💬 Please type your tax question.\n\n💡 # - Save & Menu | 0 - Cancel")
                elif text == '2':
                    send_whatsapp(from_number, "💳 *AI Credits Balance*\n\nYou have 10 credits remaining.\n\nBuy more with Option 6.")
                elif text == '3':
                    send_whatsapp(from_number, "📋 *Current Plan*\n\nYou are on the Free Plan.\n\nReply 4 to view upgrade options.")
                elif text == '4':
                    plans = get_plans_list_menu()
                    send_whatsapp(from_number, plans)
                elif text == '5':
                    send_whatsapp(from_number, "🔗 *Link Website Account*\n\nVisit www.naijataxguides.com/settings to link your account.")
                elif text == '6':
                    send_whatsapp(from_number, "💰 *Buy AI Credits*\n\nVisit www.naijataxguides.com/credits to purchase.")
                elif text == '7':
                    send_whatsapp(from_number, get_tax_menu())
                elif text == '8':
                    send_whatsapp(from_number, get_help_menu())
                elif text == '#':
                    send_whatsapp(from_number, get_main_menu())
                elif text == '9':
                    send_whatsapp(from_number, get_main_menu())
                elif text == '*':
                    send_whatsapp(from_number, get_main_menu())
                elif text == '0':
                    send_whatsapp(from_number, "❌ Cancelled. Send # for main menu.")
                elif text.isdigit() and len(text) >= 5:
                    try:
                        salary = float(text.replace(',', ''))
                        data = calculate_paye(salary)
                        result = f"""*PAYE SUMMARY*

Gross: ₦{data['gross']:,.0f}
Pension: ₦{data['pension']:,.0f}
NHF: ₦{data['nhf']:,.0f}
Tax: ₦{data['tax']:,.0f}
Net: *₦{data['net']:,.0f}*
Rate: {data['rate']}%"""
                        send_whatsapp(from_number, result)
                    except:
                        send_whatsapp(from_number, "Please enter a valid number (e.g., 500000)")
                else:
                    send_whatsapp(from_number, get_main_menu())
        
        return "ok"
    except Exception as e:
        logging.error(f"Error: {e}")
        return "error", 500

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8000))
    app.run(host='0.0.0.0', port=port)