# app/routes/channel_payment_return.py
from __future__ import annotations

import logging
from flask import Blueprint, request, jsonify, redirect
from urllib.parse import urlencode

from app.services.outbound_service import send_whatsapp_text, send_telegram_text
from app.services.paystack_service import verify_transaction
from app.services.channel_subscription_service import store_user_email, get_user_subscription
from app.services.channel_credit_service import add_credits_to_account

logger = logging.getLogger(__name__)

bp = Blueprint("channel_payment_return", __name__)


@bp.route("/channel/payment/return", methods=["GET", "POST"])
def channel_payment_return():
    """Handle payment callback from Paystack for channel users"""
    
    # Extract parameters (works for both GET and POST)
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        reference = data.get("reference", "")
    else:
        reference = request.args.get("reference", "")
    
    channel_type = request.args.get("channel_type", "") or request.form.get("channel_type", "")
    provider_user_id = request.args.get("provider_user_id", "") or request.form.get("provider_user_id", "")
    account_id = request.args.get("account_id", "") or request.form.get("account_id", "")
    plan_code = request.args.get("plan_code", "") or request.form.get("plan_code", "")
    
    if not reference:
        return jsonify({"ok": False, "error": "missing_reference"}), 400
    
    # Verify transaction with Paystack
    try:
        tx_data = verify_transaction(reference)
        status = tx_data.get("data", {}).get("status", "")
        
        if status == "success":
            # Send success notification to user
            message = (
                f"✅ *PAYMENT SUCCESSFUL!*\n\n"
                f"Reference: {reference}\n"
                f"Thank you for your payment!\n\n"
                f"Your subscription/credits have been activated.\n"
                f"You can now continue using Naija Tax Guide."
            )
            
            if channel_type == "whatsapp" and provider_user_id:
                send_whatsapp_text(provider_user_id, message)
            elif channel_type == "telegram" and provider_user_id:
                send_telegram_text(provider_user_id, message)
            
            # Redirect to success page or return JSON
            return jsonify({
                "ok": True, 
                "message": "Payment successful! Your subscription/credits are now active.",
                "reference": reference
            }), 200
        else:
            # Payment not successful
            error_message = f"❌ *PAYMENT NOT COMPLETED*\n\nReference: {reference}\nStatus: {status}\n\nPlease try again or contact support."
            
            if channel_type == "whatsapp" and provider_user_id:
                send_whatsapp_text(provider_user_id, error_message)
            elif channel_type == "telegram" and provider_user_id:
                send_telegram_text(provider_user_id, error_message)
            
            return jsonify({"ok": False, "error": f"payment_{status}"}), 400
            
    except Exception as e:
        logger.error(f"Payment verification error: {e}")
        error_message = f"❌ *PAYMENT VERIFICATION FAILED*\n\nReference: {reference}\n\nPlease contact support with your reference number."
        
        if channel_type == "whatsapp" and provider_user_id:
            send_whatsapp_text(provider_user_id, error_message)
        elif channel_type == "telegram" and provider_user_id:
            send_telegram_text(provider_user_id, error_message)
        
        return jsonify({"ok": False, "error": str(e)}), 500


# Simple HTML success page for browser redirects
@bp.route("/channel/payment/success")
def payment_success():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Payment Successful - Naija Tax Guide</title>
        <style>
            body { font-family: Arial, sans-serif; text-align: center; padding: 50px; }
            .success { color: green; font-size: 48px; }
            .message { margin-top: 20px; font-size: 18px; }
        </style>
    </head>
    <body>
        <div class="success">✅</div>
        <h1>Payment Successful!</h1>
        <p class="message">Your payment has been processed successfully.</p>
        <p>You can now close this window and return to WhatsApp/Telegram.</p>
    </body>
    </html>
    """


@bp.route("/channel/payment/cancel")
def payment_cancel():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Payment Cancelled - Naija Tax Guide</title>
        <style>
            body { font-family: Arial, sans-serif; text-align: center; padding: 50px; }
            .cancel { color: orange; font-size: 48px; }
            .message { margin-top: 20px; font-size: 18px; }
        </style>
    </head>
    <body>
        <div class="cancel">⚠️</div>
        <h1>Payment Cancelled</h1>
        <p class="message">You cancelled the payment process.</p>
        <p>You can return to WhatsApp/Telegram and try again.</p>
    </body>
    </html>
    """
