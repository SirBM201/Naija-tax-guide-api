# app/services/channel_subscription_service.py
from __future__ import annotations

import uuid
import os
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, Tuple

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
            
            # Determine billing cycle
            if "yearly" in plan_code or duration_days >= 365:
                billing_cycle = "yearly"
                cycle_emoji = "📅"
                cycle_text = "per year"
            elif "quarterly" in plan_code or duration_days >= 90:
                billing_cycle = "quarterly"
                cycle_emoji = "📆"
                cycle_text = "per quarter"
            else:
                billing_cycle = "monthly"
                cycle_emoji = "📆"
                cycle_text = "per month"
            
            # Get base name
            base_name = row.get("name", plan_code.split("_")[0] if "_" in plan_code else plan_code).title()
            
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
                "keywords": [
                    base_name.lower(),
                    plan_code.lower(),
                    f"{base_name} {billing_cycle}".lower(),
                    f"{base_name.lower()} monthly",
                    f"{base_name.lower()} quarterly", 
                    f"{base_name.lower()} yearly",
                    billing_cycle
                ]
            }
            index += 1
        
        return plans
    except Exception as e:
        logger.error(f"Error fetching plans: {e}")
        return {}


def detect_plan_from_text(text: str) -> Tuple[Optional[int], Optional[Dict[str, Any]]]:
    """Detect plan from user input (number, name, or amount)"""
    text_lower = text.lower().strip()
    plans = get_plans_from_db()
    
    # Check by number first
    if text_lower.isdigit():
        num = int(text_lower)
        if num in plans:
            return num, plans[num]
    
    # Check by amount (e.g., "5000", "₦5000", "N5000")
    import re
    amount_match = re.search(r'(\d{4,})', text_lower)
    if amount_match:
        amount = int(amount_match.group(1))
        for num, plan in plans.items():
            if plan["amount_ngn"] == amount:
                return num, plan
    
    # Check by name keywords
    for num, plan in plans.items():
        for keyword in plan.get("keywords", []):
            if keyword in text_lower:
                return num, plan
    
    return None, None


def get_plans_list_menu() -> str:
    """Complete plans menu with numbers"""
    plans = get_plans_from_db()
    
    if not plans:
        return "📋 *Available Plans*\n\nNo plans available at the moment. Please check back later."
    
    menu_lines = ["📋 *AVAILABLE SUBSCRIPTION PLANS*\n", "Reply with the plan number (e.g., '1'):\n"]
    
    for num, plan in plans.items():
        billing_display = {"monthly": "Monthly", "quarterly": "Quarterly", "yearly": "Yearly"}.get(plan["billing_cycle"], plan["billing_cycle"])
        menu_lines.append(f"{num}️⃣ *{plan['name']} {billing_display}*")
        menu_lines.append(f"   💰 ₦{plan['amount_ngn']:,} per {plan['billing_cycle']}")
        menu_lines.append(f"   🎯 {plan['credits']} AI credits")
        menu_lines.append(f"   📊 {plan['daily_limit']} questions/day")
        menu_lines.append("")
    
    menu_lines.append("Or type the plan name (e.g., 'Starter Monthly')")
    menu_lines.append("Enter 0 to cancel.")
    return "\n".join(menu_lines)


def validate_plan_number(plan_num: int) -> Optional[Dict[str, Any]]:
    """Validate plan number and return plan details"""
    plans = get_plans_from_db()
    return plans.get(plan_num)


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
        "📧 *Email Required*\n\n"
        "To activate your subscription, please provide your email address.\n"
        "We need this for payment receipts and subscription management.\n\n"
        "Send your email address (e.g., example@gmail.com):"
    )


def create_subscription_payment(
    account_id: str,
    plan: Dict[str, Any],
    channel_type: str,
    provider_user_id: str,
    email: Optional[str] = None
) -> Dict[str, Any]:
    """Create a Paystack subscription payment"""
    
    if not email:
        return {
            "ok": False,
            "error": "email_required",
            "message": request_email_message(),
            "awaiting_email": True,
            "plan": plan
        }
    
    # Validate email
    if "@" not in email or "." not in email or len(email) < 5:
        return {
            "ok": False,
            "error": "invalid_email",
            "message": "❌ Invalid email address. Please send a valid email (e.g., name@example.com)"
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
            billing_display = {"monthly": "month", "quarterly": "3 months", "yearly": "year"}.get(plan["billing_cycle"], plan["billing_cycle"])
            renewal_text = {"yearly": "yearly", "quarterly": "quarterly", "monthly": "monthly"}.get(plan["billing_cycle"], "monthly")
            
            return {
                "ok": True,
                "payment_link": result["data"]["authorization_url"],
                "reference": reference,
                "amount_ngn": plan["amount_ngn"],
                "plan_name": plan["full_name"],
                "credits": plan["credits"],
                "message": f"💎 *{plan['full_name']} Subscription*\n\n"
                          f"💰 Amount: ₦{plan['amount_ngn']:,}\n"
                          f"🎯 Credits: {plan['credits']} AI credits per {billing_display}\n"
                          f"📊 Daily limit: {plan['daily_limit']} questions\n"
                          f"🔄 Auto-renews {renewal_text}\n\n"
                          f"🔗 Click to pay:\n{result['data']['authorization_url']}\n\n"
                          f"✅ Payment confirms your subscription\n"
                          f"📧 Receipts will be sent to: {email}"
            }
        else:
            return {
                "ok": False,
                "error": "payment_link_failed",
                "message": "❌ Could not generate payment link. Please try again."
            }
    except Exception as e:
        logger.error(f"Error creating subscription: {e}")
        return {
            "ok": False,
            "error": str(e),
            "message": "❌ Payment service error. Please try again later."
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


def has_active_subscription(account_id: str) -> bool:
    """Check if user has an active subscription"""
    sub = get_user_subscription(account_id)
    if not sub:
        return False
    
    # Check if subscription is still valid
    expires_at = sub.get("current_period_end")
    if expires_at:
        try:
            exp_dt = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
            if exp_dt < datetime.now(timezone.utc):
                return False
        except:
            pass
    
    return True


def format_subscription_message(account_id: str) -> str:
    """Format subscription status message"""
    subscription = get_user_subscription(account_id)
    
    if subscription:
        plan_code = subscription.get("plan_code", "unknown")
        expires_at = subscription.get("current_period_end", "")
        
        # Try to get plan details
        plans = get_plans_from_db()
        plan_details = None
        for plan in plans.values():
            if plan["code"] == plan_code:
                plan_details = plan
                break
        
        if plan_details:
            plan_name = plan_details["full_name"]
            credits = plan_details["credits"]
        else:
            plan_name = plan_code.replace("_monthly", "").replace("_quarterly", "").replace("_yearly", "")
            credits = "?"
        
        expiry_text = ""
        if expires_at:
            try:
                dt = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
                expiry_text = f"\n📅 Next billing: {dt.strftime('%b %d, %Y')}"
            except:
                pass
        
        return (f"📋 *YOUR SUBSCRIPTION*\n\n"
                f"✅ Plan: {plan_name}\n"
                f"🎯 Monthly Credits: {credits} AI credits\n"
                f"{expiry_text}\n\n"
                f"🔄 Auto-renews automatically\n"
                f"💡 You have unlimited AI access with your plan!\n\n"
                f"To cancel, contact support.")
    else:
        return ("📋 *NO ACTIVE SUBSCRIPTION*\n\n"
                "You are on the Free plan.\n"
                "🎯 Free: 10 AI credits\n\n"
                "Reply with 4 to see available plans and upgrade.")


def get_credit_balance_with_subscription(account_id: str, base_balance: int) -> Tuple[int, str]:
    """Get credit balance considering active subscription"""
    if has_active_subscription(account_id):
        # Users with active subscription have unlimited access
        return 999999, "unlimited"
    return base_balance, "limited"
