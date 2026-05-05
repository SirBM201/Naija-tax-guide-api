# app/routes/paystack_webhook.py
from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from flask import Blueprint, jsonify, request
from datetime import datetime, timezone

from app.core.supabase_client import supabase
from app.services.paystack_service import verify_webhook_signature
from app.services.channel_subscription_service import activate_subscription
from app.services.channel_credit_service import add_credits_to_account
from app.services.outbound_service import send_whatsapp_text, send_telegram_text

logger = logging.getLogger(__name__)

bp = Blueprint("paystack_webhook", __name__)


def _sb():
    return supabase() if callable(supabase) else supabase


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _send_channel_notification(channel_type: str, provider_user_id: str, message: str):
    """Send notification to user's channel"""
    try:
        if channel_type == "whatsapp" and provider_user_id:
            send_whatsapp_text(provider_user_id, message)
        elif channel_type == "telegram" and provider_user_id:
            send_telegram_text(provider_user_id, message)
    except Exception as e:
        logger.error(f"Error sending notification: {e}")


@bp.post("/paystack/webhook")
def paystack_webhook():
    raw = request.get_data() or b""
    sig = _clean(request.headers.get("x-paystack-signature"))

    # Verify signature (optional - uncomment in production)
    # if not verify_webhook_signature(raw, sig):
    #     return jsonify({"ok": False, "error": "invalid_signature"}), 401

    payload: Dict[str, Any] = request.get_json(silent=True) or {}
    event_type = _clean(payload.get("event"))
    data = payload.get("data") or {}
    reference = _clean(data.get("reference"))
    status = _clean(data.get("status")).lower()
    metadata = data.get("metadata") or {}

    logger.info(f"Paystack webhook: event={event_type}, reference={reference}, status={status}")

    # Only process successful charge events
    if event_type not in ["charge.success", "subscription.create", "invoice.payment_succeeded"]:
        return jsonify({"ok": True, "ignored": True}), 200
    
    if status != "success":
        return jsonify({"ok": True, "ignored": True}), 200

    # Extract metadata
    account_id = _clean(metadata.get("account_id"))
    plan_code = _clean(metadata.get("plan_code"))
    credits = metadata.get("credits", 0)
    transaction_type = _clean(metadata.get("type", "credit_purchase"))
    channel_type = _clean(metadata.get("channel_type"))
    provider_user_id = _clean(metadata.get("provider_user_id"))
    amount_ngn = metadata.get("amount_ngn", 0)

    if not account_id:
        logger.error(f"Missing account_id for reference: {reference}")
        return jsonify({"ok": False, "error": "missing_account_id"}), 400

    # Process based on transaction type
    if transaction_type == "credit_purchase" and credits > 0:
        success = add_credits_to_account(account_id, credits, reference)
        if success:
            message = f"✅ *{credits} CREDITS ADDED!*\n\nYour payment of ₦{amount_ngn:,} for {credits} AI credits has been confirmed.\n\n💡 Reply with 2 to check your balance.\n💡 Reply with 7 for menu."
            _send_channel_notification(channel_type, provider_user_id, message)
            logger.info(f"Added {credits} credits to {account_id}")
        else:
            logger.error(f"Failed to add credits to {account_id}")
    
    elif transaction_type == "subscription" or plan_code:
        result = activate_subscription(account_id, plan_code, reference)
        
        if result.get("ok"):
            # Get plan details for better message
            plan_display = plan_code.replace("_", " ").title()
            
            message = f"✅ *SUBSCRIPTION ACTIVATED!*\n\n"
            message += f"📋 Plan: {plan_display}\n"
            message += f"💰 Amount: ₦{amount_ngn:,}\n"
            message += f"🆔 Reference: {reference}\n\n"
            message += f"✨ You now have UNLIMITED AI credits!\n"
            message += f"💡 Reply with 3 to check your plan status.\n"
            message += f"💡 Reply with 7 for menu."
            
            _send_channel_notification(channel_type, provider_user_id, message)
            logger.info(f"Subscription activated for {account_id}: {plan_code}")
        else:
            logger.error(f"Failed to activate subscription for {account_id}: {result.get('error')}")
            message = f"❌ *SUBSCRIPTION ACTIVATION FAILED*\n\nYour payment was received but we couldn't activate your subscription.\n\nPlease contact support with reference: {reference}"
            _send_channel_notification(channel_type, provider_user_id, message)

    # Update transaction record
    try:
        _sb().table("paystack_transactions") \
            .update({
                "status": "success",
                "paystack_status": status,
                "updated_at": datetime.now(timezone.utc).isoformat()
            }) \
            .eq("reference", reference) \
            .execute()
    except Exception as e:
        logger.error(f"Error updating transaction: {e}")

    return jsonify({"ok": True, "processed": True}), 200
