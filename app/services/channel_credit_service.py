# app/services/channel_credit_service.py
from __future__ import annotations

import uuid
import os
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from app.core.supabase_client import supabase
from app.services.paystack_service import initialize_transaction
from app.core.config import PAYSTACK_CURRENCY

logger = logging.getLogger(__name__)

# Credit packages
CREDIT_PACKAGES = {
    1: {"credits": 10, "amount_ngn": 500, "amount_kobo": 50000, "description": "10 AI Credits"},
    2: {"credits": 50, "amount_ngn": 2000, "amount_kobo": 200000, "description": "50 AI Credits"},
    3: {"credits": 100, "amount_ngn": 3500, "amount_kobo": 350000, "description": "100 AI Credits"},
    4: {"credits": 500, "amount_ngn": 15000, "amount_kobo": 1500000, "description": "500 AI Credits"},
}


def _sb():
    return supabase() if callable(supabase) else supabase


def get_credit_balance(account_id: str) -> int:
    """Get current credit balance for an account"""
    try:
        result = _sb().table("ai_credit_balances") \
            .select("balance") \
            .eq("account_id", account_id) \
            .limit(1) \
            .execute()
        
        if result.data:
            return result.data[0].get("balance", 0)
        return 0
    except Exception as e:
        logger.error(f"Error getting credit balance: {e}")
        return 0


def get_credit_packages_menu() -> str:
    """Get the credit packages menu text for WhatsApp/Telegram"""
    return (
        "💎 *Buy AI Credits*\n\n"
        "Reply with the package number:\n\n"
        "1️⃣ - 10 credits - ₦500\n"
        "2️⃣ - 50 credits - ₦2,000\n"
        "3️⃣ - 100 credits - ₦3,500\n"
        "4️⃣ - 500 credits - ₦15,000\n\n"
        "Enter 0 to cancel."
    )


def validate_package_number(package_num: int) -> Optional[Dict[str, Any]]:
    """Validate package number and return package details"""
    return CREDIT_PACKAGES.get(package_num)


def get_or_create_account_id(channel_type: str, provider_user_id: str) -> str:
    """
    Get or create an account ID for a channel user (WhatsApp/Telegram)
    This ensures every user has an account_id even without email
    """
    try:
        # Look up existing account
        result = _sb().table("accounts") \
            .select("account_id") \
            .eq("provider", channel_type) \
            .eq("provider_user_id", provider_user_id) \
            .limit(1) \
            .execute()
        
        if result.data:
            account_id = result.data[0].get("account_id")
            if account_id:
                return account_id
        
        # Create new account if none exists
        new_account_id = str(uuid.uuid4())
        _sb().table("accounts").insert({
            "id": new_account_id,
            "account_id": new_account_id,
            "provider": channel_type,
            "provider_user_id": provider_user_id,
            "created_at": datetime.now(timezone.utc).isoformat()
        }).execute()
        
        return new_account_id
        
    except Exception as e:
        logger.error(f"Error getting/creating account: {e}")
        # Fallback: use provider_user_id as identifier
        return provider_user_id


def create_credit_payment(
    account_id: str, 
    package_num: int, 
    channel_type: str,
    provider_user_id: str
) -> Dict[str, Any]:
    """
    Create a Paystack payment for credit purchase (NO EMAIL REQUIRED)
    
    Args:
        account_id: The user's account ID
        package_num: 1, 2, 3, or 4
        channel_type: 'whatsapp' or 'telegram'
        provider_user_id: User's WhatsApp/Telegram ID
    
    Returns:
        Dict with payment link and reference
    """
    package = CREDIT_PACKAGES.get(package_num)
    if not package:
        return {
            "ok": False,
            "error": "invalid_package",
            "message": "Invalid package number. Please select 1-4."
        }
    
    reference = f"CREDIT_{package['credits']}_{uuid.uuid4().hex[:8]}"
    amount_kobo = package["amount_kobo"]
    credits = package["credits"]
    amount_ngn = package["amount_ngn"]
    
    # Store transaction record with channel user info
    try:
        _sb().table("paystack_transactions").insert({
            "reference": reference,
            "account_id": account_id,
            "amount": amount_kobo,
            "currency": PAYSTACK_CURRENCY or "NGN",
            "status": "pending",
            "plan_code": f"credits_{credits}",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "metadata": {
                "account_id": account_id,
                "credits": credits,
                "package": package_num,
                "type": "credit_purchase",
                "channel_type": channel_type,
                "provider_user_id": provider_user_id
            }
        }).execute()
    except Exception as e:
        logger.error(f"Error storing transaction: {e}")
    
    try:
        # Initialize Paystack transaction with NO EMAIL (pass None)
        result = initialize_transaction(
            amount=amount_kobo,
            email=None,  # ✅ NO EMAIL REQUIRED
            reference=reference,
            metadata={
                "account_id": account_id,
                "credits": credits,
                "package": package_num,
                "type": "credit_purchase",
                "channel_type": channel_type,
                "provider_user_id": provider_user_id
            }
        )
        
        if result.get("status") and result.get("data", {}).get("authorization_url"):
            return {
                "ok": True,
                "payment_link": result["data"]["authorization_url"],
                "reference": reference,
                "amount_ngn": amount_ngn,
                "credits": credits,
                "message": f"💰 *Payment Link*\n\nClick to pay ₦{amount_ngn:,} for {credits} AI credits:\n\n{result['data']['authorization_url']}\n\n✅ After payment, your credits will be added automatically.\n\n💡 No email needed - we'll identify you via WhatsApp!"
            }
        else:
            error_msg = result.get("message", "Payment initialization failed")
            return {
                "ok": False,
                "error": "payment_link_failed",
                "message": f"Could not generate payment link: {error_msg}\n\nPlease try again later."
            }
    except Exception as e:
        logger.error(f"Error creating payment link: {e}")
        return {
            "ok": False,
            "error": str(e),
            "message": "Payment service error. Please try again later."
        }


def add_credits_to_account(account_id: str, credits: int, reference: str) -> bool:
    """Add credits to user's balance after successful payment"""
    try:
        existing = _sb().table("ai_credit_balances") \
            .select("balance") \
            .eq("account_id", account_id) \
            .execute()
        
        if existing.data:
            new_balance = existing.data[0].get("balance", 0) + credits
            _sb().table("ai_credit_balances") \
                .update({
                    "balance": new_balance,
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }) \
                .eq("account_id", account_id) \
                .execute()
        else:
            _sb().table("ai_credit_balances").insert({
                "account_id": account_id,
                "balance": credits,
                "updated_at": datetime.now(timezone.utc).isoformat()
            }).execute()
        
        # Log credit addition
        _sb().table("ai_credit_events").insert({
            "account_id": account_id,
            "event_type": "credit_purchase",
            "credits": credits,
            "reference": reference,
            "created_at": datetime.now(timezone.utc).isoformat()
        }).execute()
        
        logger.info(f"Added {credits} credits to account {account_id} via {reference}")
        return True
        
    except Exception as e:
        logger.error(f"Error adding credits: {e}")
        return False


def format_balance_message(balance: int) -> str:
    """Format balance message for WhatsApp/Telegram"""
    if balance == 0:
        return ("💎 *AI Credits Balance*\n\n"
                "You have *0 credits* remaining.\n\n"
                "Each credit = 1 AI tax question.\n\n"
                "To buy credits, reply with 6.")
    else:
        return (f"💎 *AI Credits Balance*\n\n"
                f"You have *{balance} credits* remaining.\n\n"
                f"Each credit = 1 AI tax question.\n\n"
                f"To buy more credits, reply with 6.")


def get_user_email_status(account_id: str) -> Dict[str, Any]:
    """
    Optional: Check if user has an email associated with their account
    This can be used to optionally request email only if needed
    """
    try:
        result = _sb().table("accounts") \
            .select("email") \
            .eq("account_id", account_id) \
            .limit(1) \
            .execute()
        
        if result.data and result.data[0].get("email"):
            return {"has_email": True, "email": result.data[0]["email"]}
        return {"has_email": False, "email": None}
    except Exception as e:
        logger.error(f"Error checking email status: {e}")
        return {"has_email": False, "email": None}


def request_email_optional(account_id: str, channel_type: str, provider_user_id: str) -> str:
    """
    Optional: Politely ask for email only for receipt delivery
    This does NOT block payment processing
    """
    return (
        "📧 *Optional: Email for Receipts*\n\n"
        "To receive payment receipts via email (optional), reply with your email address.\n\n"
        "Or reply 'skip' to continue without email.\n\n"
        "Your credits will be added either way after payment."
    )
