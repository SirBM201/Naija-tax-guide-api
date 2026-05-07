import os
import re
import logging
import json
import hmac
import hashlib
from flask import Flask, request, jsonify
import requests
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# ============ TELEGRAM CONFIGURATION ============
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# Debug logging for Telegram token
if TELEGRAM_TOKEN:
    logging.info(f"✅ TELEGRAM_TOKEN loaded. Length: {len(TELEGRAM_TOKEN)}")
else:
    logging.error("❌ TELEGRAM_TOKEN NOT FOUND in environment variables!")

# ============ WHATSAPP CONFIGURATION ============
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "your_verify_token_here")
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
WHATSAPP_API_URL = "https://graph.facebook.com/v18.0"

if WHATSAPP_ACCESS_TOKEN:
    logging.info(f"✅ WHATSAPP_ACCESS_TOKEN loaded. Length: {len(WHATSAPP_ACCESS_TOKEN)}")
else:
    logging.warning("⚠️ WHATSAPP_ACCESS_TOKEN not configured - WhatsApp features disabled")

# ============ TAX CALCULATION FUNCTION (Shared) ============
def calculate_nigerian_paye(monthly_gross):
    """Calculate Nigerian PAYE tax based on PITA"""
    annual_gross = monthly_gross * 12
    
    # Pension (8% of monthly gross)
    pension = monthly_gross * 0.08
    
    # NHF (2.5% of monthly gross)
    nhf = monthly_gross * 0.025
    
    # Consolidated Relief Allowance (CRA)
    cra_fixed = 200000
    cra_one_percent = annual_gross * 0.01
    cra_base = max(cra_fixed, cra_one_percent)
    cra_percentage = annual_gross * 0.20
    cra_total = cra_base + cra_percentage
    
    # Annual deductions
    annual_pension = pension * 12
    annual_nhf = nhf * 12
    total_annual_deductions = annual_pension + annual_nhf + cra_total
    
    # Chargeable Income
    chargeable_income = annual_gross - total_annual_deductions
    chargeable_income = max(0, chargeable_income)
    
    # Nigerian Tax Bands (Annual)
    if chargeable_income <= 300000:
        annual_tax = chargeable_income * 0.07
    elif chargeable_income <= 600000:
        annual_tax = 21000 + (chargeable_income - 300000) * 0.11
    elif chargeable_income <= 1100000:
        annual_tax = 54000 + (chargeable_income - 600000) * 0.15
    elif chargeable_income <= 1600000:
        annual_tax = 129000 + (chargeable_income - 1100000) * 0.19
    elif chargeable_income <= 3200000:
        annual_tax = 224000 + (chargeable_income - 1600000) * 0.21
    else:
        annual_tax = 560000 + (chargeable_income - 3200000) * 0.24
    
    # Minimum tax rule
    if annual_tax < (annual_gross * 0.01) and annual_gross > 0:
        annual_tax = annual_gross * 0.01
    
    monthly_tax = annual_tax / 12
    effective_rate = (annual_tax / annual_gross) * 100 if annual_gross > 0 else 0
    
    return {
        "monthly_gross": monthly_gross,
        "annual_gross": annual_gross,
        "pension": round(pension, 2),
        "nhf": round(nhf, 2),
        "cra": round(cra_total / 12, 2),
        "total_monthly_deductions": round(pension + nhf, 2),
        "chargeable_income_monthly": round(chargeable_income / 12, 2),
        "chargeable_income_annual": round(chargeable_income, 2),
        "annual_tax": round(annual_tax, 2),
        "monthly_tax": round(monthly_tax, 2),
        "effective_rate": round(effective_rate, 2),
        "net_pay": round(monthly_gross - pension - nhf - monthly_tax, 2)
    }

# ============ TELEGRAM FUNCTIONS ============
def format_tax_summary_telegram(data):
    """Format Nigerian tax calculation for Telegram"""
    return f"""
🇳🇬 *NIGERIA PAYE TAX SUMMARY*

📊 *Monthly Gross:* ₦{data['monthly_gross']:,.2f}
📈 *Annual Gross:* ₦{data['annual_gross']:,.2f}

📋 *Monthly Deductions:*
• Pension (8%): ₦{data['pension']:,.2f}
• NHF (2.5%): ₦{data['nhf']:,.2f}
• CRA Relief: ₦{data['cra']:,.2f}
• *Total Deductions:* ₦{data['total_monthly_deductions']:,.2f}

🎯 *Chargeable Income:*
• Monthly: ₦{data['chargeable_income_monthly']:,.2f}
• Annual: ₦{data['chargeable_income_annual']:,.2f}

🧾 *Tax Due:*
• *Annual Tax:* ₦{data['annual_tax']:,.2f}
• *Monthly Tax:* ₦{data['monthly_tax']:,.2f}
• *Effective Rate:* {data['effective_rate']}%

💵 *Net Monthly Take-home:* ₦{data['net_pay']:,.2f}
"""

def send_telegram_message(chat_id, text):
    """Send message via Telegram API"""
    if not TELEGRAM_TOKEN:
        logging.error("Cannot send message: TELEGRAM_TOKEN is missing")
        return False
    
    url = f"{TELEGRAM_API_URL}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        logging.info(f"Message sent successfully to chat_id: {chat_id}")
        return True
    except Exception as e:
        logging.error(f"Failed to send message: {e}")
        return False

# ============ WHATSAPP FUNCTIONS ============
def verify_whatsapp_webhook(mode, token, challenge):
    """Verify webhook for WhatsApp Cloud API"""
    if mode and token:
        if mode == "subscribe" and token == WHATSAPP_VERIFY_TOKEN:
            return challenge
    return None

def send_whatsapp_message(to_number, text):
    """Send message via WhatsApp Cloud API"""
    if not WHATSAPP_ACCESS_TOKEN or not PHONE_NUMBER_ID:
        logging.error("WHATSAPP_ACCESS_TOKEN or PHONE_NUMBER_ID not configured")
        return False
    
    url = f"{WHATSAPP_API_URL}/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_number,
        "type": "text",
        "text": {"body": text}
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        response.raise_for_status()
        logging.info(f"WhatsApp message sent to {to_number}")
        return True
    except Exception as e:
        logging.error(f"Failed to send WhatsApp message: {e}")
        return False

def format_tax_summary_whatsapp(monthly_salary):
    """Format tax calculation for WhatsApp (simpler format)"""
    data = calculate_nigerian_paye(monthly_salary)
    
    return f"""🇳🇬 *NIGERIA PAYE TAX SUMMARY*

Monthly Gross: ₦{data['monthly_gross']:,.2f}
Annual Gross: ₦{data['annual_gross']:,.2f}

*Monthly Deductions:*
• Pension (8%): ₦{data['pension']:,.2f}
• NHF (2.5%): ₦{data['nhf']:,.2f}
• CRA Relief: ₦{data['cra']:,.2f}

*Taxable Income:*
Monthly: ₦{data['chargeable_income_monthly']:,.2f}

*Tax Due:*
Annual Tax: ₦{data['annual_tax']:,.2f}
Monthly Tax: ₦{data['monthly_tax']:,.2f}
Effective Rate: {data['effective_rate']}%

*Net Monthly Pay:* ₦{data['net_pay']:,.2f}

Reply with another amount to calculate again."""

def process_whatsapp_message(message_data):
    """Process incoming WhatsApp message"""
    try:
        entry = message_data.get('entry', [{}])[0]
        changes = entry.get('changes', [{}])[0]
        value = changes.get('value', {})
        messages = value.get('messages', [])
        
        if not messages:
            return None, None
        
        message = messages[0]
        from_number = message.get('from')
        msg_type = message.get('type')
        
        if msg_type == 'text':
            text = message.get('text', {}).get('body', '').strip()
            return from_number, text
        
        return from_number, None
        
    except Exception as e:
        logging.error(f"Error processing WhatsApp message: {e}")
        return None, None

# ============ FLASK ENDPOINTS ============

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({"status": "healthy", "telegram": bool(TELEGRAM_TOKEN), "whatsapp": bool(WHATSAPP_ACCESS_TOKEN)}), 200

@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    """Handle incoming Telegram messages"""
    try:
        update = request.get_json()
        
        if not update or 'message' not in update:
            return jsonify({"status": "ok"}), 200
        
        message = update['message']
        chat_id = message['chat']['id']
        text = message.get('text', '').strip()
        
        logging.info(f"Telegram message from {chat_id}: {text}")
        
        if text == '/start':
            welcome = """
🇳🇬 *Nigerian PAYE Tax Calculator*

Send me your monthly salary (e.g., `500000` or `500,000`)

I'll calculate:
• Pension & NHF deductions
• Consolidated Relief Allowance (CRA)
• Monthly & Annual tax
• Net take-home pay

Based on Nigerian PITA tax bands.
"""
            send_telegram_message(chat_id, welcome)
            return jsonify({"status": "ok"}), 200
        
        salary_match = re.search(r'[\d,]+', text.replace(',', ''))
        
        if salary_match:
            monthly_salary = float(salary_match.group())
            if monthly_salary <= 0:
                send_telegram_message(chat_id, "Please enter a positive amount.")
            else:
                tax_data = calculate_nigerian_paye(monthly_salary)
                summary = format_tax_summary_telegram(tax_data)
                send_telegram_message(chat_id, summary)
        else:
            send_telegram_message(chat_id, 
                "Please send a valid monthly salary.\nExample: `250000` or `350,000`")
        
        return jsonify({"status": "ok"}), 200
        
    except Exception as e:
        logging.error(f"Telegram webhook error: {e}")
        return jsonify({"status": "error"}), 500

@app.route('/api/whatsapp/webhook', methods=['GET', 'POST'])
def whatsapp_webhook():
    """Handle WhatsApp webhook verification and messages"""
    
    # GET request = Webhook verification
    if request.method == 'GET':
        mode = request.args.get('hub.mode')
        token = request.args.get('hub.verify_token')
        challenge = request.args.get('hub.challenge')
        
        result = verify_whatsapp_webhook(mode, token, challenge)
        if result:
            logging.info("WhatsApp webhook verified successfully")
            return result, 200
        else:
            logging.error("WhatsApp webhook verification failed")
            return "Verification failed", 403
    
    # POST request = Incoming message
    elif request.method == 'POST':
        try:
            body = request.get_json()
            logging.info(f"WhatsApp webhook received")
            
            from_number, message_text = process_whatsapp_message(body)
            
            if from_number and message_text:
                salary_match = re.search(r'[\d,]+', message_text.replace(',', ''))
                
                if salary_match:
                    monthly_salary = float(salary_match.group())
                    if monthly_salary > 0:
                        response = format_tax_summary_whatsapp(monthly_salary)
                        send_whatsapp_message(from_number, response)
                    else:
                        send_whatsapp_message(from_number, "Please send a valid positive amount.")
                elif message_text.lower() in ['/start', 'start', 'help']:
                    welcome = """Welcome to Nigerian PAYE Tax Calculator! 🇳🇬

Send me your monthly salary to calculate:
• Pension & NHF deductions
• CRA Relief
• Monthly & Annual tax
• Net take-home pay

Example: 500000 or 250,000"""
                    send_whatsapp_message(from_number, welcome)
                else:
                    send_whatsapp_message(from_number, "Please send a valid monthly salary.\nExample: 500000 or 250,000")
            
            return jsonify({"status": "ok"}), 200
            
        except Exception as e:
            logging.error(f"WhatsApp webhook error: {e}")
            return jsonify({"status": "error"}), 500

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8000))
    app.run(host='0.0.0.0', port=port)