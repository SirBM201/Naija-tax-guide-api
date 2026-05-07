# Add these imports at the top (if not already there)
import json
import hashlib
import hmac

# ============ WHATSAPP CONFIGURATION ============
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "your_verify_token_here")
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
WHATSAPP_API_URL = "https://graph.facebook.com/v18.0"

def verify_whatsapp_webhook(mode, token, challenge):
    """Verify webhook for WhatsApp Cloud API"""
    if mode and token:
        if mode == "subscribe" and token == WHATSAPP_VERIFY_TOKEN:
            return challenge
    return None

def send_whatsapp_message(to_number, text):
    """Send message via WhatsApp Cloud API"""
    if not WHATSAPP_ACCESS_TOKEN:
        logging.error("WHATSAPP_ACCESS_TOKEN not configured")
        return False
    
    url = f"{WHATSAPP_API_URL}/YOUR_PHONE_NUMBER_ID/messages"
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

def process_whatsapp_message(message_data):
    """Process incoming WhatsApp message and return tax calculation"""
    try:
        # Extract message details
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
        elif msg_type == 'interactive':
            # Handle button responses
            interactive = message.get('interactive', {})
            if interactive.get('type') == 'button_reply':
                text = interactive.get('button_reply', {}).get('title', '')
                return from_number, text
        
        return from_number, None
        
    except Exception as e:
        logging.error(f"Error processing WhatsApp message: {e}")
        return None, None

def format_whatsapp_response(monthly_salary):
    """Format tax calculation for WhatsApp (simpler format than Telegram)"""
    tax_data = calculate_nigerian_paye(monthly_salary)
    
    response = f"""🇳🇬 *NIGERIA PAYE TAX SUMMARY*

Monthly Gross: ₦{tax_data['monthly_gross']:,.2f}
Annual Gross: ₦{tax_data['annual_gross']:,.2f}

*Monthly Deductions:*
• Pension (8%): ₦{tax_data['pension']:,.2f}
• NHF (2.5%): ₦{tax_data['nhf']:,.2f}
• CRA Relief: ₦{tax_data['cra']:,.2f}

*Taxable Income:*
Monthly: ₦{tax_data['chargeable_income_monthly']:,.2f}

*Tax Due:*
Annual Tax: ₦{tax_data['annual_tax']:,.2f}
Monthly Tax: ₦{tax_data['monthly_tax']:,.2f}
Effective Rate: {tax_data['effective_rate']}%

*Net Monthly Pay:* ₦{tax_data['net_pay']:,.2f}

Reply with another amount to calculate again."""
    
    return response

# ============ WHATSAPP WEBHOOK ENDPOINTS ============

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
            logging.info(f"WhatsApp webhook received: {json.dumps(body, indent=2)}")
            
            # Process the message
            from_number, message_text = process_whatsapp_message(body)
            
            if from_number and message_text:
                # Parse salary from message
                salary_match = re.search(r'[\d,]+', message_text.replace(',', ''))
                
                if salary_match:
                    monthly_salary = float(salary_match.group())
                    if monthly_salary > 0:
                        response = format_whatsapp_response(monthly_salary)
                        send_whatsapp_message(from_number, response)
                    else:
                        send_whatsapp_message(from_number, "Please send a valid positive amount.")
                elif message_text.lower() in ['/start', 'start', 'help']:
                    welcome = """Welcome to Nigerian PAYE Tax Calculator! 🇳🇬

Send me your monthly salary to calculate:
• Pension & NHF deductions
• Consolidated Relief Allowance (CRA)
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