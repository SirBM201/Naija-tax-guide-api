# app/services/channel_subscription_service.py
from __future__ import annotations

import uuid
import os
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List

from app.core.supabase_client import supabase
from app.services.paystack_service import initialize_transaction

logger = logging.getLogger(__name__)


def _sb():
    return supabase() if callable(supabase) else supabase


def get_plans_from_db() -> Dict[int, Dict[str, Any]]:
    """Fetch actual plans from database - includes all billing cycles"""
    try:
        result = _sb().table("plans") \
            .select("plan_code, name, price, ai_credits_total, daily_answers_limit, duration_days") \
            .eq("active", True) \
            .execute()
        
        plans = {}
        index = 1
        for row in (result.data or []):
            plan_code = row.get("plan_code", "")
            duration_days = row.get("duration_days", 30)
            
            # Determine billing cycle from plan_code or duration
            if "yearly" in plan_code or duration_days >= 365:
                billing_cycle = "yearly"
                cycle_emoji = "📅"
                cycle_text = "per year"
                discount = " (Save ~17%)"
            elif "quarterly" in plan_code or duration_days >= 90:
                billing_cycle = "quarterly"
                cycle_emoji = "📆"
                cycle_text = "per quarter"
                discount = " (Save ~10%)"
            else:
                billing_cycle = "monthly"
                cycle_emoji = "📆"
                cycle_text = "per month"
                discount = ""
            
            # Get base name (without billing cycle suffix)
            base_name = row.get("name", plan_code.replace("_monthly", "").replace("_quarterly", "").replace("_yearly", "")).title()
            
            plans[index] = {
                "code": plan_code,
                "name": base_name,
                "full_name": f"{base_name} {billing_cycle.capitalize()}",
                "amount_ngn": row.get("price", 0),
                "amount_kobo": row.get("price", 0) * 100,
                "credits": row.get("ai_credits_total", 0),
                "daily_limit": row.get("daily_answers_limit", 0),
                "duration_days": duration_days,
                "billing_cycle": billing_cycle,
                "cycle_text": cycle_text,
                "cycle_emoji": cycle_emoji,
                "discount": discount,
                "description": f"{base_name} {billing_cycle.capitalize()} - {row.get('ai_credits_total', 0)} AI credits {cycle_text}"
            }
            index += 1
        
        return plans
    except Exception as e:
        logger.error(f"Error fetching plans: {e}")
        # Fallback to hardcoded plans
        return {
            1: {"code": "starter_monthly", "name": "Starter", "full_name": "Starter Monthly", "amount_ngn": 5000, "amount_kobo": 500000, "credits": 100, "daily_limit": 10, "duration_days": 30, "billing_cycle": "monthly", "cycle_text": "per month", "cycle_emoji": "📆", "discount": "", "description": "Starter Monthly - 100 AI credits per month"},
            2: {"code": "starter_quarterly", "name": "Starter", "full_name": "Starter Quarterly", "amount_ngn": 14000, "amount_kobo": 1400000, "credits": 300, "daily_limit": 10, "duration_days": 90, "billing_cycle": "quarterly", "cycle_text": "per quarter", "cycle_emoji": "📆", "discount": " (Save ~7%)", "description": "Starter Quarterly - 300 AI credits per quarter"},
            3: {"code": "starter_yearly", "name": "Starter", "full_name": "Starter Yearly", "amount_ngn": 51000, "amount_kobo": 5100000, "credits": 1200, "daily_limit": 10, "duration_days": 365, "billing_cycle": "yearly", "cycle_text": "per year", "cycle_emoji": "📅", "discount": " (Save ~15%)", "description": "Starter Yearly - 1200 AI credits per year"},
            4: {"code": "professional_monthly", "name": "Professional", "full_name": "Professional Monthly", "amount_ngn": 12000, "amount_kobo": 1200000, "credits": 300, "daily_limit": 20, "duration_days": 30, "billing_cycle": "monthly", "cycle_text": "per month", "cycle_emoji": "📆", "discount": "", "description": "Professional Monthly - 300 AI credits per month"},
            5: {"code": "professional_quarterly", "name": "Professional", "full_name": "Professional Quarterly", "amount_ngn": 33600, "amount_kobo": 3360000, "credits": 900, "daily_limit": 20, "duration_days": 90, "billing_cycle": "quarterly", "cycle_text": "per quarter", "cycle_emoji": "📆", "discount": " (Save ~7%)", "description": "Professional Quarterly - 900 AI credits per quarter"},
            6: {"code": "professional_yearly", "name": "Professional", "full_name": "Professional Yearly", "amount_ngn": 122400, "amount_kobo": 12240000, "credits": 3600, "daily_limit": 20, "duration_days": 365, "billing_cycle": "yearly", "cycle_text": "per year", "cycle_emoji": "📅", "discount": " (Save ~15%)", "description": "Professional Yearly - 3600 AI credits per year"},
            7: {"code": "business_monthly", "name": "Business", "full_name": "Business Monthly", "amount_ngn": 25000, "amount_kobo": 2500000, "credits": 800, "daily_limit": 50, "duration_days": 30, "billing_cycle": "monthly", "cycle_text": "per month", "cycle_emoji": "📆", "discount": "", "description": "Business Monthly - 800 AI credits per month"},
            8: {"code": "business_quarterly", "name": "Business", "full_name": "Business Quarterly", "amount_ngn": 70000, "amount_kobo": 7000000, "credits": 2400, "daily_limit": 50, "duration_days": 90, "billing_cycle": "quarterly", "cycle_text": "per quarter", "cycle_emoji": "📆", "discount": " (Save ~7%)", "description": "Business Quarterly - 2400 AI credits per quarter"},
            9: {"code": "business_yearly", "name": "Business", "full_name": "Business Yearly", "amount_ngn": 255000, "amount_kobo": 25500000, "credits": 9600, "daily_limit": 50, "duration_days": 365, "billing_cycle": "yearly", "cycle_text": "per year", "cycle_emoji": "📅", "discount": " (Save ~15%)", "description": "Business Yearly - 9600 AI credits per year"},
        }


def get_plans_by_tier() -> Dict[str, List[Dict[str, Any]]]:
    """Group plans by tier (Starter, Professional, Business)"""
    all_plans = get_plans_from_db()
    grouped = {}
    
    for plan in all_plans.values():
        tier = plan["name"]
        if tier not in grouped:
            grouped[tier] = []
        grouped[tier].append(plan)
    
    return grouped


def get_subscription_plans_menu() -> str:
    """Get subscription plans menu organized by tier with billing options"""
    grouped_plans = get_plans_by_tier()
    
    menu_lines = ["📋 *Subscription Plans*\n\nChoose your plan and billing cycle:\n"]
    
    for tier, plans in grouped_plans.items():
        menu_lines.append(f"\n*{tier} Plan*")
        for plan in plans:
            billing_display = {
                "monthly": "Monthly",
                "quarterly": "Quarterly (save 7%)",
                "yearly": "Yearly (save 15%)"
            }.get(plan["billing_cycle"], plan["billing_cycle"].capitalize())
            
            menu_lines.append(f"  • {billing_display}: ₦{plan['amount_ngn']:,} - {plan['credits']} credits")
    
    menu_lines.append("\n\nTo subscribe, reply with the plan code (e.g., 'starter_monthly')")
    menu_lines.append("Or reply with the number from the list below:")
    
    # Add numbered list
    all_plans = get_plans_from_db()
    menu_lines.append("\n*Quick select:*")
    for num, plan in all_plans.items():
        menu_lines.append(f"{num}️⃣ - {plan['full_name']} - ₦{plan['amount_ngn']:,}")
    
    menu_lines.append("\nEnter 0 to cancel.")
    return "\n".join(menu_lines)


def get_plans_list_menu() -> str:
    """Simple numbered list of all plans"""
    plans = get_plans_from_db()
    
    menu_lines = ["📋 *Available Plans*\n\nReply with the plan number:\n"]
    
    for num, plan in plans.items():
        emoji = {1: "1️⃣", 2: "2️⃣", 3: "3️⃣", 4: "4️⃣", 5: "5️⃣", 6: "6️⃣", 7: "7️⃣", 8: "8️⃣", 9: "9️⃣"}.get(num, f"{num}️⃣")
        menu_lines.append(f"{emoji} - {plan['full_name']} - ₦{plan['amount_ngn']:,} ({plan['credits']} credits)")
    
    menu_lines.append("\nEnter 0 to cancel.")
    return "\n".join(menu_lines)


def validate_plan_number(plan_num: int) -> Optional[Dict[str, Any]]:
    """Validate plan number and return plan details from database"""
    plans = get_plans_from_db()
    return plans.get(plan_num)


def validate_plan_code(plan_code: str) -> Optional[Dict[str, Any]]:
    """Validate plan code and return plan details"""
    plans = get_plans_from_db()
    for plan in plans.values():
        if plan["code"] == plan_code:
            return plan
    return None


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
        "To set up your subscription (auto-renewing), we need your email address for payment receipts and subscription management.\n\n"
        "Please send your email address (e.g., example@gmail.com):"
    )


def create_subscription_payment(
    account_id: str,
    plan_identifier,  # Can be int (1-9) or string (plan_code)
    channel_type: str,
    provider_user_id: str,
    email: Optional[str] = None
) -> Dict[str, Any]:
    """
    Create a Paystack subscription payment
    
    Args:
        account_id: User's account ID
        plan_identifier: Plan number (1-9) or plan code (e.g., 'starter_monthly')
        channel_type: 'whatsapp' or 'telegram'
        provider_user_id: User's channel ID
        email: Optional - required for recurring subscription
    
    Returns:
        Dict with payment link and reference
    """
    # Determine if plan_identifier is number or string code
    if isinstance(plan_identifier, int):
        plan = validate_plan_number(plan_identifier)
    else:
        plan = validate_plan_code(str(plan_identifier))
    
    if not plan:
        return {
            "ok": False,
            "error": "invalid_plan",
            "message": "Invalid plan selection. Please select a valid plan."
        }
    
    # Check if email is needed but not provided
    if not email:
        return {
            "ok": False,
            "error": "email_required",
            "message": request_email_message(),
            "awaiting_email": True,
            "plan_code": plan["code"]
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
            # Determine renewal text based on billing cycle
            if plan["billing_cycle"] == "yearly":
                renewal_text = "🔄 Auto-renews yearly"
            elif plan["billing_cycle"] == "quarterly":
                renewal_text = "🔄 Auto-renews quarterly"
            else:
                renewal_text = "🔄 Auto-renews monthly"
            
            return {
                "ok": True,
                "payment_link": result["data"]["authorization_url"],
                "reference": reference,
                "amount_ngn": plan["amount_ngn"],
                "plan_name": plan["full_name"],
                "credits": plan["credits"],
                "daily_limit": plan.get("daily_limit", 0),
                "billing_cycle": plan["billing_cycle"],
                "message": f"💎 *{plan['full_name']} Subscription*\n\n"
                          f"Click to pay ₦{plan['amount_ngn']:,} for {plan['full_name']}:\n\n"
                          f"{result['data']['authorization_url']}\n\n"
                          f"✅ Plan includes: {plan['credits']} AI credits {plan['cycle_text']}\n"
                          f"📊 Daily limit: {plan.get('daily_limit', 0)} questions/day\n"
                          f"{renewal_text}\n"
                          f"📧 Receipts to: {email}"
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
        
        # Get plan details for better display
        plan = validate_plan_code(plan_code)
        
        if plan:
            plan_name = plan["full_name"]
            credits = plan["credits"]
            billing_text = {
                "monthly": "monthly",
                "quarterly": "quarterly",
                "yearly": "yearly"
            }.get(plan["billing_cycle"], plan["billing_cycle"])
        else:
            plan_name = plan_code.replace("_monthly", "").replace("_quarterly", "").replace("_yearly", "").title()
            credits = "?"
            billing_text = "monthly"
        
        # Format expiry date if available
        expiry_text = ""
        if expires_at:
            try:
                dt = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
                expiry_text = f"\n📅 Next billing: {dt.strftime('%b %d, %Y')}"
            except:
                pass
        
        return (f"📋 *Your Current Plan*\n\n"
                f"Plan: {plan_name}\n"
                f"Monthly Credits: {credits} AI credits\n"
                f"Status: Active ✅\n"
                f"{expiry_text}\n\n"
                f"🔄 Auto-renews {billing_text}\n"
                f"To cancel, contact support.")
    else:
        return ("📋 *Your Current Plan*\n\n"
                "Plan: Free\n"
                "Status: Active ✅\n"
                "AI Credits: 10/month\n"
                "Daily Limit: 5 questions/day\n\n"
                "Reply with 4 to see available plans.")
