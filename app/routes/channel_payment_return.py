# app/routes/channel_payment_return.py
from __future__ import annotations

import logging
from flask import Blueprint, request, jsonify, redirect
from urllib.parse import urlencode

from app.services.outbound_service import send_whatsapp_text, send_telegram_text
from app.services.paystack_service import verify_transaction

logger = logging.getLogger(__name__)

bp = Blueprint("channel_payment_return", __name__)


def _get_whatsapp_deeplink(phone_number: str, message: str = "") -> str:
    """Generate WhatsApp deep link to return user to chat"""
    # Remove any non-digit characters from phone number
    import re
    clean_number = re.sub(r'\D', '', phone_number)
    if not clean_number.startswith('234'):
        if clean_number.startswith('0'):
            clean_number = '234' + clean_number[1:]
        else:
            clean_number = '234' + clean_number
    
    if message:
        encoded_msg = urlencode({"text": message})
        return f"https://wa.me/{clean_number}?{encoded_msg}"
    return f"https://wa.me/{clean_number}"


def _get_telegram_deeplink(username: str = "naijataxguide_bot") -> str:
    """Generate Telegram deep link to return user to bot"""
    return f"https://t.me/{username}"


@bp.route("/channel/payment/return", methods=["GET", "POST"])
def channel_payment_return():
    """Handle payment callback from Paystack for channel users"""
    
    # Extract parameters
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        reference = data.get("reference", "")
    else:
        reference = request.args.get("reference", "")
    
    channel_type = request.args.get("channel_type", "") or request.form.get("channel_type", "")
    provider_user_id = request.args.get("provider_user_id", "") or request.form.get("provider_user_id", "")
    account_id = request.args.get("account_id", "") or request.form.get("account_id", "")
    plan_code = request.args.get("plan_code", "") or request.form.get("plan_code", "")
    trxref = request.args.get("trxref", "")  # Paystack also sends this
    
    # Use trxref if reference not found
    if not reference and trxref:
        reference = trxref
    
    if not reference:
        return jsonify({"ok": False, "error": "missing_reference"}), 400
    
    # Verify transaction with Paystack
    try:
        tx_data = verify_transaction(reference)
        status = tx_data.get("data", {}).get("status", "")
        
        # Send confirmation message to user via their channel
        if status == "success":
            success_message = (
                f"✅ *PAYMENT SUCCESSFUL!*\n\n"
                f"Reference: {reference}\n\n"
                f"Your subscription/credits have been activated.\n"
                f"You can now continue using Naija Tax Guide.\n\n"
                f"Reply with 7 to see the menu."
            )
            
            # Send message to user on their channel
            if channel_type == "whatsapp" and provider_user_id:
                send_whatsapp_text(provider_user_id, success_message)
                # Return user to WhatsApp chat
                return redirect(_get_whatsapp_deeplink(provider_user_id))
            
            elif channel_type == "telegram" and provider_user_id:
                send_telegram_text(provider_user_id, success_message)
                return redirect(_get_telegram_deeplink())
            
            # Fallback - show success page with button
            return """
            <!DOCTYPE html>
            <html>
            <head>
                <title>Payment Successful - Naija Tax Guide</title>
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <style>
                    body { font-family: Arial, sans-serif; text-align: center; padding: 50px; }
                    .success { color: green; font-size: 48px; }
                    .message { margin-top: 20px; font-size: 18px; }
                    .button { display: inline-block; margin-top: 30px; padding: 12px 24px; background: #25D366; color: white; text-decoration: none; border-radius: 8px; }
                    .button-wa { background: #25D366; }
                </style>
            </head>
            <body>
                <div class="success">✅</div>
                <h1>Payment Successful!</h1>
                <p class="message">Your payment has been processed successfully.</p>
                <a href="https://wa.me/{}" class="button button-wa">📱 Return to WhatsApp</a>
            </body>
            </html>
            """.format(provider_user_id if provider_user_id else "")
        
        else:
            # Payment not successful
            error_message = f"❌ *PAYMENT NOT COMPLETED*\n\nReference: {reference}\nStatus: {status}\n\nPlease try again or contact support.\n\nReply with 4 to see plans or 6 to buy credits."
            
            if channel_type == "whatsapp" and provider_user_id:
                send_whatsapp_text(provider_user_id, error_message)
                return redirect(_get_whatsapp_deeplink(provider_user_id))
            elif channel_type == "telegram" and provider_user_id:
                send_telegram_text(provider_user_id, error_message)
                return redirect(_get_telegram_deeplink())
            
            return jsonify({"ok": False, "error": f"payment_{status}"}), 400
            
    except Exception as e:
        logger.error(f"Payment verification error: {e}")
        error_message = f"❌ *PAYMENT VERIFICATION FAILED*\n\nReference: {reference}\n\nPlease contact support with your reference number."
        
        if channel_type == "whatsapp" and provider_user_id:
            send_whatsapp_text(provider_user_id, error_message)
        elif channel_type == "telegram" and provider_user_id:
            send_telegram_text(provider_user_id, error_message)
        
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/channel/payment/success")
def payment_success():
    """Simple HTML success page with return to WhatsApp button"""
    phone = request.args.get("phone", "")
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Payment Successful - Naija Tax Guide</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ font-family: Arial, sans-serif; text-align: center; padding: 50px; }}
            .success {{ color: green; font-size: 48px; }}
            .message {{ margin-top: 20px; font-size: 18px; }}
            .button {{ display: inline-block; margin-top: 30px; padding: 12px 24px; background: #25D366; color: white; text-decoration: none; border-radius: 8px; font-weight: bold; }}
            .button:hover {{ opacity: 0.9; }}
        </style>
    </head>
    <body>
        <div class="success">✅</div>
        <h1>Payment Successful!</h1>
        <p class="message">Your payment has been processed successfully.<br>Your subscription/credits are now active.</p>
        <a href="https://wa.me/{phone}" class="button">📱 Return to WhatsApp Chat</a>
        <p style="margin-top: 30px; color: #666;">You can also close this window and open WhatsApp manually.</p>
    </body>
    </html>
    """


@bp.route("/channel/payment/cancel")
def payment_cancel():
    """Payment cancellation page"""
    phone = request.args.get("phone", "")
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Payment Cancelled - Naija Tax Guide</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ font-family: Arial, sans-serif; text-align: center; padding: 50px; }}
            .cancel {{ color: orange; font-size: 48px; }}
            .message {{ margin-top: 20px; font-size: 18px; }}
            .button {{ display: inline-block; margin-top: 30px; padding: 12px 24px; background: #25D366; color: white; text-decoration: none; border-radius: 8px; font-weight: bold; }}
        </style>
    </head>
    <body>
        <div class="cancel">⚠️</div>
        <h1>Payment Cancelled</h1>
        <p class="message">You cancelled the payment process.</p>
        <a href="https://wa.me/{phone}" class="button">📱 Return to WhatsApp Chat</a>
    </body>
    </html>
    """
