# app/services/channel_payment_service.py
from __future__ import annotations

import uuid
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from app.core.supabase_client import supabase
from app.services.paystack_service import initialize_transaction, create_reference
from app.core.config import PAYSTACK_SECRET_KEY, PAYSTACK_CURRENCY

logger = logging.getLogger(__name__)

# Credit packages
CREDIT_PACKAGES = {
    1: {"credits": 10, "amount": 500, "description": "10 AI Credits"},
    2: {"credits": 50, "amount": 2000, "description": "50 AI Credits"},
    3: {"credits": 100, "amount": 3500, "description": "100 AI Credits"},
    4: {"credits": 500, "amount": 15000, "description": "500 AI Credits"},
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


def create_credit_purchase_link(account_id: str, package_number: int) -> Dict[str, Any]:
    """
    Create a Paystack payment link for credit purchase
    
    Args:
        account_id: The user's account ID
        package_number: 1, 2, 3, or 4
    
    Returns:
        Dict with payment link and reference
    """
    if package_number not in CREDIT_PACKAGES:
        return {
            "ok": False,
            "error": "invalid_package",
            "message": "Please select a valid package (1-4)"
        }
    
    package = CREDIT_PACKAGES[package_number]
    amount_kobo = package["amount"] * 100  # Convert to kobo
    credits = package["credits"]
    
    # Create unique reference
    reference = create_reference(f"CREDIT_{credits}_")
    
    # Store transaction record
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
                "package": package_number,
                "type": "credit_purchase"
            }
        }).execute()
    except Exception as e:
        logger.error(f"Error storing transaction: {e}")
    
    # Initialize payment
    callback_url = os.getenv("PAYSTACK_CALLBACK_URL", "https://www.naijataxguides.com/billing")
    
    try:
        result = initialize_transaction(
            amount=amount_kobo,
            email=None,  # Email will be collected by Paystack
            reference=reference,
            metadata={
                "account_id": account_id,
                "credits": credits,
                "package": package_number,
                "type": "credit_purchase"
            },
            callback_url=callback_url
        )
        
        if result.get("status") and result.get("data", {}).get("authorization_url"):
            return {
                "ok": True,
                "payment_link": result["data"]["authorization_url"],
                "reference": reference,
                "amount": package["amount"],
                "credits": credits,
                "message": f"Click the link below to pay ₦{package['amount']:,} for {credits} AI credits:\n{result['data']['authorization_url']}"
            }
        else:
            return {
                "ok": False,
                "error": "payment_link_failed",
                "message": "Could not generate payment link. Please try again."
            }
    except Exception as e:
        logger.error(f"Error creating payment link: {e}")
        return {
            "ok": False,
            "error": str(e),
            "message": "Payment service error. Please try again later."
        }


def add_credits_after_payment(reference: str, credits: int) -> bool:
    """Add credits to user's balance after successful payment"""
    try:
        # Get transaction to find account_id
        result = _sb().table("paystack_transactions") \
            .select("account_id, metadata") \
            .eq("reference", reference) \
            .limit(1) \
            .execute()
        
        if not result.data:
            logger.error(f"Transaction not found for reference: {reference}")
            return False
        
        account_id = result.data[0].get("account_id")
        if not account_id:
            logger.error(f"No account_id for reference: {reference}")
            return False
        
        # Update or insert credit balance
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
        
        logger.info(f"Added {credits} credits to account {account_id}")
        return True
        
    except Exception as e:
        logger.error(f"Error adding credits: {e}")
        return False


def get_credit_packages_menu() -> str:
    """Get the credit packages menu text"""
    return (
        "*Buy AI Credits* 💎\n\n"
        "Choose a package:\n"
        "1️⃣ - 10 credits - ₦500\n"
        "2️⃣ - 50 credits - ₦2,000\n"
        "3️⃣ - 100 credits - ₦3,500\n"
        "4️⃣ - 500 credits - ₦15,000\n\n"
        "Reply with the package number (1-4) to proceed."
    )
