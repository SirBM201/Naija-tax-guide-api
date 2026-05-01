# app/routes/paystack_webhook.py
from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from flask import Blueprint, jsonify, request
from datetime import datetime, timezone, timedelta

from app.core.supabase_client import supabase
from app.services.paystack_service import verify_webhook_signature
from app.services.outbound_service import send_whatsapp_text, send_telegram_text

logger = logging.getLogger(__name__)

bp = Blueprint("paystack_webhook", __name__)


def _sb():
    return supabase() if callable(supabase) else supabase


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _add_credits_to_account(account_id: str, credits: int, reference: str) -> bool:
    """Add credits to user's balance after successful payment"""
    try:
        existing = _sb().table("ai_credit_balances") \
            .select("balance") \
            .eq("account_id", account_id) \
            .execute()
        
        now = datetime.now(timezone.utc).isoformat()
        
        if existing.data:
            new_balance = existing.data[0].get("balance", 0) + credits
            _sb().table("ai_credit_balances") \
                .update({
                    "balance": new_balance,
                    "updated_at": now
                }) \
                .eq("account_id", account_id) \
                .execute()
        else:
            _sb().table("ai_credit_balances").insert({
                "account_id": account_id,
                "balance": credits,
                "updated_at": now
            }).execute()
        
        logger.info(f"Added {credits} credits to account {account_id}")
        return True
        
    except Exception as e:
        logger.error(f"Error adding credits: {e}")
        return False


def _activate_subscription(account_id: str, plan_code: str, reference: str) -> Dict[str, Any]:
    """Activate a subscription for a user"""
    try:
        now = datetime.now(timezone.utc)
        
        # Determine duration based on plan_code
        if "yearly" in plan_code:
            duration_days = 365
            billing_cycle = "yearly"
        elif "quarterly" in plan_code:
            duration_days = 90
            billing_cycle = "quarterly"
        else:
            duration_days = 30
            billing_cycle = "monthly"
        
        end_date = now + timedelta(days=duration_days)
        
        # Check if subscription exists
        existing = _sb().table("user_subscriptions") \
            .select("*") \
            .eq("account_id", account_id) \
            .execute()
        
        if existing.data:
            # Update existing subscription
            _sb().table("user_subscriptions") \
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
            _sb().table("user_subscriptions").insert({
                "account_id": account_id,
                "plan_code": plan_code,
                "status": "active",
                "current_period_end": end_date.isoformat(),
                "created_at": now.isoformat(),
                "updated_at": now.isoformat()
            }).execute()
        
        logger.info(f"Subscription activated for {account_id}: {plan_code}")
        return {"ok": True, "plan_code": plan_code, "expires_at": end_date.isoformat(), "billing_cycle": billing_cycle}
        
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
        success = _add_credits_to_account(account_id, credits, reference)
        if success:
            message = f"✅ *{credits} CREDITS ADDED!*\n\nYour payment of ₦{amount_ngn:,} for {credits} AI credits has been confirmed.\n\n💡 Reply with 2 to check your balance.\n💡 Reply with 7 for menu."
            _send_channel_notification(channel_type, provider_user_id, message)
            logger.info(f"Added {credits} credits to {account_id}")
    
    elif transaction_type == "subscription" or plan_code:
        result = _activate_subscription(account_id, plan_code, reference)
        
        if result.get("ok"):
            billing_display = {"monthly": "month", "quarterly": "3 months", "yearly": "year"}.get(result.get("billing_cycle", "monthly"), "month")
            plan_display = plan_code.replace("_", " ").title()
            
            message = f"✅ *SUBSCRIPTION ACTIVATED!*\n\n📋 Plan: {plan_display}\n💰 Amount: ₦{amount_ngn:,}\n📅 Valid for: {billing_display}\n🔄 Auto-renews {result.get('billing_cycle', 'monthly')}\n\n✨ You now have UNLIMITED AI credits!\n💡 Reply with 3 to check your plan status.\n💡 Reply with 7 for menu."
            _send_channel_notification(channel_type, provider_user_id, message)
            logger.info(f"Subscription activated for {account_id}: {plan_code}")

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
