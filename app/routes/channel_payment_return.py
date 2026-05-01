# app/routes/channel_payment_return.py
from __future__ import annotations

from flask import Blueprint, request, jsonify
from app.services.outbound_service import send_whatsapp_text, send_telegram_text
from app.services.paystack_service import verify_transaction
from app.services.channel_subscription_service import get_user_subscription
from app.services.channel_credit_service import add_credits_to_account

bp = Blueprint("channel_payment_return", __name__)


@bp.get("/channel/payment/return")
def channel_payment_return():
    """Handle payment callback from Paystack for channel users"""
    reference = request.args.get("reference", "")
    channel_type = request.args.get("channel_type", "")
    provider_user_id = request.args.get("provider_user_id", "")
    account_id = request.args.get("account_id", "")
    plan_code = request.args.get("plan_code", "")
    
    if not reference:
        return jsonify({"ok": False, "error": "missing_reference"}), 400
    
    # Verify transaction
    try:
        tx_data = verify_transaction(reference)
        if tx_data.get("data", {}).get("status") == "success":
            # Send confirmation to user via channel
            if channel_type == "whatsapp" and provider_user_id:
                send_whatsapp_text(
                    provider_user_id,
                    f"✅ *Payment Successful!*\n\n"
                    f"Your payment has been confirmed.\n"
                    f"Reference: {reference}\n\n"
                    f"Thank you for using Naija Tax Guide!"
                )
            elif channel_type == "telegram" and provider_user_id:
                send_telegram_text(
                    provider_user_id,
                    f"✅ *Payment Successful!*\n\n"
                    f"Your payment has been confirmed.\n"
                    f"Reference: {reference}\n\n"
                    f"Thank you for using Naija Tax Guide!"
                )
    except Exception as e:
        pass
    
    # Redirect to appropriate page or return success
    return jsonify({"ok": True, "message": "Payment processed"})
