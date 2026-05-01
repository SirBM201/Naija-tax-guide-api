# app/services/channel_subscription_service.py
from __future__ import annotations

import uuid
import os
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional

from app.core.supabase_client import supabase
from app.services.paystack_service import initialize_transaction

logger = logging.getLogger(__name__)

# Available plans
PLANS = {
    1: {"code": "basic", "name": "Basic", "amount_ngn": 5000, "amount_kobo": 500000, "credits": 100, "description": "Basic Plan - 100 AI credits/month"},
    2: {"code": "pro", "name": "Pro", "amount_ngn": 15000, "amount_kobo": 1500000, "credits": 500, "description": "Pro Plan - 500 AI credits/month"},
    3: {"code": "business", "name": "Business", "amount_ngn": 50000, "amount_kobo": 5000000, "credits": 2000, "description": "Business Plan - 2000 AI credits/month"},
}


def _sb():
    return supabase() if callable(supabase) else supabase


def get_subscription_plans_menu() -> str:
    """Get subscription plans menu for WhatsApp/Telegram"""
    return (
        "📋 *Subscription Plans*\n\n"
        "Reply with the plan number:\n\n"
        "1️⃣ - Basic Plan - ₦5,000/month (100 credits)\n"
        "2️⃣ - Pro Plan - ₦15,000/month (500 credits)\n"
        "3️⃣ - Business Plan - ₦50,000/month (2000 credits)\n\n"
        "Enter 0 to cancel."
    )


def validate_plan_number(plan_num: int) -> Optional[Dict[str, Any]]:
    """Validate plan number and return plan details"""
    return PLANS.get(plan_num)


def get_user_email(account_id: str) -> Optional[str]:
    """Get user's stored email if available"""
    try:
        result = _sb().table("accounts") \
            .select("email") \
            .eq("account_id", account_id) \
            .limit(1) \
            .execute()
        
        if result.data and result.data[0].get("email"):
            return result.data[0]["email"]
        return None
    except Exception:
        return None


def store_user_email(account_id: str, email: str) -> bool:
    """Store user's email for future subscription use"""
    try:
        _sb().table("accounts") \
            .update({"email": email.lower()}) \
            .eq("account_id", account_id) \
            .execute()
        return True
    except Exception as e:
        logger.error(f"Error storing email: {e}")
        return False


def request_email_message() -> str:
    """Message to request email from user"""
    return (
        "📧 *Email Required for Subscription*\n\n"
        "To set up your monthly subscription (auto-renewing), we need your email address for payment receipts and subscription management.\n\n"
        "Please send your email address (e.g., example@gmail.com):"
    )


def create_subscription_payment(
    account_id: str,
    plan_num: int,
    channel_type: str,
    provider_user_id: str,
    email: Optional[str] = None
) -> Dict[str, Any]:
    """
    Create a Paystack subscription payment
    
    Args:
        account_id: User's account ID
        plan_num: 1, 2, or 3
        channel_type: 'whatsapp' or 'telegram'
        provider_user_id: User's channel ID
        email: Optional - required for recurring subscription
    
    Returns:
        Dict with payment link and reference
    """
    plan = PLANS.get(plan_num)
    if not plan:
        return {
            "ok": False,
            "error": "invalid_plan",
            "message": "Invalid plan number. Please select 1-3."
        }
    
    # Check if email is needed but not provided
    if not email:
        return {
            "ok": False,
            "error": "email_required",
            "message": request_email_message(),
            "awaiting_email": True,
            "plan_num": plan_num
        }
    
    # Validate email format
    if "@" not in email or "." not in email:
        return {
            "ok": False,
            "error": "invalid_email",
            "message": "Please send a valid email address (e.g., example@gmail.com)"
        }
    
    reference = f"SUB_{plan['code']}_{uuid.uuid4().hex[:8]}"
    amount_kobo = plan["amount_kobo"]
    
    # Store user's email
    store_user_email(account_id, email)
    
    # Store transaction record
    try:
        _sb().table("paystack_transactions").insert({
            "reference": reference,
            "account_id": account_id,
            "amount": amount_kobo,
            "currency": "NGN",
            "status": "pending",
            "plan_code": plan["code"],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "metadata": {
                "account_id": account_id,
                "plan_code": plan["code"],
                "type": "subscription",
                "channel_type": channel_type,
                "provider_user_id": provider_user_id
            }
        }).execute()
    except Exception as e:
        logger.error(f"Error storing transaction: {e}")
    
    try:
        # Initialize Paystack transaction
        result = initialize_transaction(
            amount_kobo=amount_kobo,
            email=email,
            reference=reference,
            metadata={
                "account_id": account_id,
                "plan_code": plan["code"],
                "type": "subscription",
                "channel_type": channel_type,
                "provider_user_id": provider_user_id
            }
        )
        
        if result.get("status") and result.get("data", {}).get("authorization_url"):
            return {
                "ok": True,
                "payment_link": result["data"]["authorization_url"],
                "reference": reference,
                "amount_ngn": plan["amount_ngn"],
                "plan_name": plan["name"],
                "credits": plan["credits"],
                "message": f"💎 *{plan['name']} Subscription*\n\n"
                          f"Click to pay ₦{plan['amount_ngn']:,} for {plan['name']} plan:\n\n"
                          f"{result['data']['authorization_url']}\n\n"
                          f"✅ After payment, your subscription will be activated.\n"
                          f"🔄 This plan auto-renews monthly.\n"
                          f"📧 Receipts will be sent to: {email}"
            }
        else:
            error_msg = result.get("message", "Payment initialization failed")
            return {
                "ok": False,
                "error": "payment_link_failed",
                "message": f"Could not generate payment link: {error_msg}\n\nPlease try again."
            }
    except Exception as e:
        logger.error(f"Error creating subscription: {e}")
        return {
            "ok": False,
            "error": str(e),
            "message": "Payment service error. Please try again later."
        }


def get_user_subscription(account_id: str) -> Optional[Dict[str, Any]]:
    """Get user's current active subscription"""
    try:
        result = _sb().table("user_subscriptions") \
            .select("*") \
            .eq("account_id", account_id) \
            .eq("status", "active") \
            .limit(1) \
            .execute()
        
        if result.data:
            return result.data[0]
        return None
    except Exception as e:
        logger.error(f"Error getting subscription: {e}")
        return None


def format_subscription_message(subscription: Optional[Dict[str, Any]]) -> str:
    """Format subscription status message"""
    if subscription:
        plan_code = subscription.get("plan_code", "unknown")
        expires_at = subscription.get("current_period_end", "")
        
        # Format expiry date if available
        expiry_text = ""
        if expires_at:
            try:
                dt = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
                expiry_text = f"\n📅 Next billing: {dt.strftime('%b %d, %Y')}"
            except:
                pass
        
        return (f"📋 *Your Current Plan*\n\n"
                f"Plan: {plan_code.upper()}\n"
                f"Status: Active ✅\n"
                f"{expiry_text}\n\n"
                f"🔄 This plan auto-renews monthly.\n"
                f"To cancel, contact support.")
    else:
        return ("📋 *Your Current Plan*\n\n"
                "Plan: Free\n"
                "Status: Active ✅\n"
                "AI Credits: 10/month\n\n"
                "Reply with 4 to upgrade to a paid plan.")
