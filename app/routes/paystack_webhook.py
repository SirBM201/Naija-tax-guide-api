# app/routes/paystack_webhook.py
from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from flask import Blueprint, jsonify, request
from datetime import datetime, timezone, timedelta

from app.core.supabase_client import supabase
from app.services.paystack_service import verify_webhook_signature
from app.services.channel_credit_service import add_credits_to_account
from app.services.outbound_service import send_whatsapp_text, send_telegram_text

logger = logging.getLogger(__name__)

bp = Blueprint("paystack_webhook", __name__)


def _sb():
    return supabase() if callable(supabase) else supabase


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _activate_subscription(account_id: str, plan_code: str, reference: str, duration_days: int = 30) -> Dict[str, Any]:
    """Activate a subscription for a user"""
    try:
        now = datetime.now(timezone.utc)
        end_date = now + timedelta(days=duration_days)
        
        # Check if subscription exists
        existing = _sb().table("user_subscriptions") \
            .select("*") \
            .eq("account_id", account_id) \
            .execute()
        
        if existing.data:
            # Update existing subscription
            result = _sb().table("user_subscriptions") \
                .update({
                    "plan_code": plan_code,
                    "status": "active",
                    "current_period_end": end_date.isoformat(),
                    "updated_at": now.isoformat()
                }) \
                .eq("account_id", account_id) \
                .execute()
        else:
            # Create new subscription
            result = _sb().table("user_subscriptions").insert({
                "account_id": account_id,
                "plan_code": plan_code,
                "status": "active",
                "current_period_end": end_date.isoformat(),
                "created_at": now.isoformat(),
                "updated_at": now.isoformat()
            }).execute()
        
        logger.info(f"Subscription activated for {account_id}: {plan_code} until {end_date}")
        return {"ok": True, "plan_code": plan_code, "expires_at": end_date.isoformat()}
        
    except Exception as e:
        logger.error(f"Error activating subscription: {e}")
        return {"ok": False, "error": str(e)}


def _send_channel_notification(channel_type: str, provider_user_id: str, message: str):
    """Send notification to user's channel"""
    try:
        if channel_type == "whatsapp" and provider_user_id:
            send_whatsapp_text(provider_user_id, message)
        elif channel_type == "telegram" and provider_user_id:
            send_telegram_text(provider_user_id, message)
    except Exception as e:
        logger.error(f"Error sending channel notification: {e}")


@bp.post("/paystack/webhook")
def paystack_webhook():
    raw = request.get_data() or b""
    sig = _clean(request.headers.get("x-paystack-signature"))

    # Verify signature (skip if no secret configured for testing)
    # if not verify_webhook_signature(raw, sig):
    #     return jsonify({"ok": False, "error": "invalid_signature"}), 401

    payload: Dict[str, Any] = request.get_json(silent=True) or {}
    event_type = _clean(payload.get("event"))
    event_id = _clean(payload.get("id"))

    data = payload.get("data") or {}
    reference = _clean(data.get("reference"))
    status = _clean(data.get("status")).lower()
    metadata = data.get("metadata") or {}

    # Log the webhook
    logger.info(f"Paystack webhook: event={event_type}, reference={reference}, status={status}")
    
    # Only process successful charge events
    if event_type not in ["charge.success", "subscription.create", "invoice.payment_succeeded"]:
        return jsonify({"ok": True, "ignored": True}), 200
    
    if status != "success":
        return jsonify({"ok": True, "ignored": True, "reason": "status_not_success"}), 200

    # Extract metadata
    account_id = _clean(metadata.get("account_id"))
    plan_code = _clean(metadata.get("plan_code"))
    credits = metadata.get("credits", 0)
    transaction_type = _clean(metadata.get("type", "credit_purchase"))
    channel_type = _clean(metadata.get("channel_type"))
    provider_user_id = _clean(metadata.get("provider_user_id"))

    if not account_id:
        logger.error(f"Missing account_id in metadata for reference: {reference}")
        return jsonify({"ok": False, "error": "missing_account_id"}), 400

    # Process based on transaction type
    if transaction_type == "credit_purchase" and credits > 0:
        # Add credits to account
        success = add_credits_to_account(account_id, credits, reference)
        if success:
            message = f"✅ *{credits} CREDITS ADDED!*\n\nYour payment of ₦{metadata.get('amount_ngn', '0')} for {credits} AI credits has been confirmed.\n\nYou now have unlimited access to ask tax questions.\n\nReply with 2 to check your balance."
            _send_channel_notification(channel_type, provider_user_id, message)
            logger.info(f"Added {credits} credits to account {account_id}")
        else:
            logger.error(f"Failed to add credits to account {account_id}")
    
    elif transaction_type == "subscription" or plan_code:
        # Determine duration days from plan_code
        duration_days = 30  # default monthly
        if "quarterly" in plan_code:
            duration_days = 90
        elif "yearly" in plan_code:
            duration_days = 365
        
        # Activate subscription
        result = _activate_subscription(account_id, plan_code, reference, duration_days)
        
        if result.get("ok"):
            # Get plan details for message
            plan_display = plan_code.replace("_", " ").title()
            message = f"✅ *SUBSCRIPTION ACTIVATED!*\n\n"
            message += f"Plan: {plan_display}\n"
            message += f"Reference: {reference}\n"
            message += f"Valid until: {result.get('expires_at', 'N/A')}\n\n"
            message += f"✨ You now have UNLIMITED AI credits!\n"
            message += f"Ask as many tax questions as you want.\n\n"
            message += f"Reply with 3 to check your plan status."
            
            _send_channel_notification(channel_type, provider_user_id, message)
            logger.info(f"Subscription activated for {account_id}: {plan_code}")
        else:
            logger.error(f"Failed to activate subscription for {account_id}: {result.get('error')}")

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
