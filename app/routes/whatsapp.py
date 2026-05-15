# app/routes/whatsapp.py
"""WhatsApp bot routes and handlers - Converted from ws.py"""

from flask import Blueprint, request, jsonify
from datetime import datetime, timedelta
import logging
import uuid
import re
import os
from collections import defaultdict

# Import shared services
from app.services.ask_service import ask_guarded
from app.core.supabase_client import supabase_client as supabase
import requests

bp = Blueprint("whatsapp", __name__)

# Legal disclaimers
DISCLAIMER_MAIN = "AI may make mistakes. Always verify with official sources."
DISCLAIMER_AI = "AI-generated. Verify important information."
DISCLAIMER_CALC = "Estimate only. Actual tax may vary."
DISCLAIMER_FILING = "Record saved. Not an official filing with tax authorities."
DISCLAIMER_DOC = "For reference only. Not legally binding."
DISCLAIMER_CREDITS = "Transaction recorded. Contact support for issues."
DISCLAIMER_SUBSCRIPTION = "Subscription active. Auto-renews unless cancelled."

# WhatsApp configuration
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "naija-tax-guide-verify")
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
WHATSAPP_API_URL = "https://graph.facebook.com/v18.0"

# Credit packages
CREDIT_PACKAGES = {
    "T10": {"credits": 10, "amount_ngn": 500, "amount_kobo": 50000, "code": "T10", "description": "10 AI Credits", "requires_subscription": True},
    "T50": {"credits": 50, "amount_ngn": 2000, "amount_kobo": 200000, "code": "T50", "description": "50 AI Credits", "requires_subscription": True},
    "T100": {"credits": 100, "amount_ngn": 3500, "amount_kobo": 350000, "code": "T100", "description": "100 AI Credits", "requires_subscription": True},
    "T500": {"credits": 500, "amount_ngn": 15000, "amount_kobo": 1500000, "code": "T500", "description": "500 AI Credits", "requires_subscription": True},
}

TAX_FILING_COSTS = {
    "paye_assistance": 10,
    "vat_preparation": 15,
    "cit_filing": 20,
    "document_generation_simple": 5,
    "document_generation_complex": 10,
    "filing_summary": 5
}

# In-memory cache
user_state = {}
user_cooldown = defaultdict(float)

# Paystack API URL
PAYSTACK_API_URL = "https://api.paystack.co"

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

def get_main_menu():
    return f"""Naija Tax Guide

1 - Ask a tax question
2 - Check credits balance
3 - Check my subscription
4 - View subscription plans
5 - Premium features
6 - Buy top-up credits
7 - Tax filing and management
8 - Help / Menu

Free Features:
- CALC 500000 - Calculate PAYE tax
- Database answers (50/day)

Premium (requires subscription):
- AI answers (1 credit)
- Tax filing (10-20 credits)

Commands: T10, T50, T100, T500 - Buy top-up
{DISCLAIMER_MAIN}"""

@bp.route('/whatsapp/webhook', methods=['GET', 'POST'])
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

                # Simple response for testing
                send_whatsapp(from_number, get_main_menu())

        return "ok"
    except Exception as e:
        logging.error(f"Error in webhook: {e}")
        return "error", 500
