# app/services/channel_subscription_service.py
from __future__ import annotations

import uuid
import os
import logging
from datetime import datetime, timezone, timedelta
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
            elif "quarterly" in plan_code or duration_days >= 90:
                billing_cycle = "quarterly"
            else:
                billing_cycle = "monthly"
            
            # Get base name
            base_name = row.get("name", plan_code.split("_")[0] if "_" in plan_code else plan_code).title()
            
            # Calculate credits based on billing cycle
            monthly_credits = row.get("ai_credits_total", 0)
            if billing_cycle == "quarterly":
                credits = monthly_credits * 3
            elif billing_cycle == "yearly":
                credits = monthly_credits * 12
            else:
                credits = monthly_credits
            
            plans[index] = {
                "code": plan_code,
                "name": base_name,
                "full_name": f"{base_name} {billing_cycle.capitalize()}",
                "amount_ngn": row.get("price", 0),
                "amount_kobo": row.get("price", 0) * 100,
                "credits": credits,
                "monthly_credits": monthly_credits,
                "daily_limit": row.get("daily_answers_limit", 0),
                "duration_days": duration_days,
                "billing_cycle": billing_cycle,
                "cycle_text": f"per {billing_cycle}",
                "keywords": [
                    base_name.lower(),
                    plan_code.lower(),
                    f"{base_name} {billing_cycle}".lower(),
                    billing_cycle,
                    str(row.get("price", 0))
                ]
            }
            index += 1
        
        return plans
    except Exception as e:
        logger.error(f"Error fetching plans: {e}")
        # Fallback plans
        return {
            1: {"code": "starter_monthly", "name": "Starter", "full_name": "Starter Monthly", "amount_ngn": 5000, "amount_kobo": 500000, "credits": 100, "monthly_credits": 100, "daily_limit": 10, "duration_days": 30, "billing_cycle": "monthly", "cycle_text": "per month", "keywords": ["starter", "starter monthly", "monthly"]},
            2: {"code": "starter_quarterly", "name": "Starter", "full_name": "Starter Quarterly", "amount_ngn": 14000, "amount_kobo": 1400000, "credits": 300, "monthly_credits": 100, "daily_limit": 10, "duration_days": 90, "billing_cycle": "quarterly", "cycle_text": "per quarter", "keywords": ["starter", "starter quarterly", "quarterly"]},
            3: {"code": "starter_yearly", "name": "Starter", "full_name": "Starter Yearly", "amount_ngn": 51000, "amount_kobo": 5100000, "credits": 1200, "monthly_credits": 100, "daily_limit": 10, "duration_days": 365, "billing_cycle": "yearly", "cycle_text": "per year", "keywords": ["starter", "starter yearly", "yearly"]},
            4: {"code": "professional_monthly", "name": "Professional", "full_name": "Professional Monthly", "amount_ngn": 12000, "amount_kobo": 1200000, "credits": 300, "monthly_credits": 300, "daily_limit": 20, "duration_days": 30, "billing_cycle": "monthly", "cycle_text": "per month", "keywords": ["professional", "professional monthly", "monthly"]},
            5: {"code": "professional_quarterly", "name": "Professional", "full_name": "Professional Quarterly", "amount_ngn": 33600, "amount_kobo": 3360000, "credits": 900, "monthly_credits": 300, "daily_limit": 20, "duration_days": 90, "billing_cycle": "quarterly", "cycle_text": "per quarter", "keywords": ["professional", "professional quarterly", "quarterly"]},
            6: {"code": "professional_yearly", "name": "Professional", "full_name": "Professional Yearly", "amount_ngn": 122400, "amount_kobo": 12240000, "credits": 3600, "monthly_credits": 300, "daily_limit": 20, "duration_days": 365, "billing_cycle": "yearly", "cycle_text": "per year", "keywords": ["professional", "professional yearly", "yearly"]},
            7: {"code": "business_monthly", "name": "Business", "full_name": "Business Monthly", "amount_ngn": 25000, "amount_kobo": 2500000, "credits": 800, "monthly_credits": 800, "daily_limit": 50, "duration_days": 30, "billing_cycle": "monthly", "cycle_text": "per month", "keywords": ["business", "business monthly", "monthly"]},
            8: {"code": "business_quarterly", "name": "Business", "full_name": "Business Quarterly", "amount_ngn": 70000, "amount_kobo": 7000000, "credits": 2400, "monthly_credits": 800, "daily_limit": 50, "duration_days": 90, "billing_cycle": "quarterly", "cycle_text": "per quarter", "keywords": ["business", "business quarterly", "quarterly"]},
            9: {"code": "business_yearly", "name": "Business", "full_name": "Business Yearly", "amount_ngn": 255000, "amount_kobo": 25500000, "credits": 9600, "monthly_credits": 800, "daily_limit": 50, "duration_days": 365, "billing_cycle": "yearly", "cycle_text": "per year", "keywords": ["business", "business yearly", "yearly"]},
        }


def get_plans_list_menu() -> str:
    """Simple numbered list of all plans"""
    plans = get_plans_from_db()
    
    if not plans:
        return "📋 *Available Plans*\n\nNo plans available at the moment. Please check back later."
    
    menu_lines = ["📋 *AVAILABLE SUBSCRIPTION PLANS*\n", "Reply with the plan number (e.g., '1'):\n"]
    
    for num, plan in plans.items():
        emoji = {1: "1️⃣", 2: "2️⃣", 3: "3️⃣", 4: "4️⃣", 5: "5️⃣", 6: "6️⃣", 7: "7️⃣", 8: "8️⃣", 9: "9️⃣"}.get(num, f"{num}️⃣")
        billing_display = {"monthly": "Monthly", "quarterly": "Quarterly", "yearly": "Yearly"}.get(plan["billing_cycle"], plan["billing_cycle"])
        menu_lines.append(f"{emoji} - *{plan['name']} {billing_display}* - ₦{plan['amount_ngn']:,} - {plan['credits']} credits")
    
    menu_lines.append("\nEnter 0 to cancel.")
    return "\n".join(menu_lines)


def validate_plan_number(plan_num: int) -> Optional[Dict[str, Any]]:
    """Validate plan number and return plan details"""
    plans = get_plans_from_db()
    return plans.get(plan_num)


def validate_plan_code(plan_code: str) -> Optional[Dict[str, Any]]:
    """Validate plan code and return plan details"""
    plans = get_plans_from_db()
    for plan in plans.values():
        if plan["code"] == plan_code:
            return plan
    return None


def detect_plan_from_text(text: str) -> Tuple[Optional[int], Optional[Dict[str, Any]]]:
    """Detect plan from user input (number, name, or amount)"""
    text_lower = text.lower().strip()
    plans = get_plans_from_db()
    
    # Check by number first
    if text_lower.isdigit():
        num = int(text_lower)
        if num in plans:
            return num, plans[num]
    
    # Check by amount
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
                "provider_user_id": provider_user_id,
                "amount_ngn": plan["amount_ngn"]
            }
        }).execute()
    except Exception as e:
        logger.error(f"Error storing transaction: {e}")
    
    # Build callback URL
    base_url = os.getenv("PUBLIC_BACKEND_BASE_URL", "https://incredible-nonie-bmsconcept-37359733.koyeb.app")
    callback_url = f"{base_url}/api/channel/payment/return?channel_type={channel_type}&provider_user_id={provider_user_id}&account_id={account_id}&plan_code={plan['code']}"
    
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
                "provider_user_id": provider_user_id,
                "amount_ngn": plan["amount_ngn"]
            },
            callback_url=callback_url
        )
        
        if result.get("status") and result.get("data", {}).get("authorization_url"):
            billing_display = {"monthly": "month", "quarterly": "3 months", "yearly": "year"}.get(plan["billing_cycle"], "month")
            return {
                "ok": True,
                "payment_link": result["data"]["authorization_url"],
                "reference": reference,
                "amount_ngn": plan["amount_ngn"],
                "plan_name": plan["full_name"],
                "credits": plan["credits"],
                "monthly_credits": plan.get("monthly_credits", plan["credits"]),
                "message": f"💎 *{plan['full_name']} Subscription*\n\n"
                          f"💰 Amount: ₦{plan['amount_ngn']:,}\n"
                          f"🎯 Credits: {plan['credits']} AI credits per {billing_display}\n"
                          f"📊 Daily limit: {plan['daily_limit']} questions\n"
                          f"🔄 Auto-renews {plan['billing_cycle']}\n\n"
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


def activate_subscription(account_id: str, plan_code: str, reference: str) -> Dict[str, Any]:
    """Activate a subscription for a user"""
    try:
        now = datetime.now(timezone.utc)
        
        # Determine duration and get plan details
        if "yearly" in plan_code:
            duration_days = 365
        elif "quarterly" in plan_code:
            duration_days = 90
        else:
            duration_days = 30
        
        current_period_end = (now + timedelta(days=duration_days)).isoformat()
        now_iso = now.isoformat()
        
        # First, deactivate any existing active subscriptions for this account
        _sb().table("user_subscriptions") \
            .update({"is_active": False, "status": "inactive", "updated_at": now_iso}) \
            .eq("account_id", account_id) \
            .eq("is_active", True) \
            .execute()
        
        # Check if a subscription already exists for this account
        existing = _sb().table("user_subscriptions") \
            .select("*") \
            .eq("account_id", account_id) \
            .order("created_at", desc=True) \
            .limit(1) \
            .execute()
        
        if existing.data:
            # Update existing subscription
            result = _sb().table("user_subscriptions") \
                .update({
                    "plan_code": plan_code,
                    "status": "active",
                    "is_active": True,
                    "current_period_end": current_period_end,
                    "updated_at": now_iso
                }) \
                .eq("id", existing.data[0]["id"]) \
                .execute()
        else:
            # Create new subscription
            result = _sb().table("user_subscriptions").insert({
                "account_id": account_id,
                "plan_code": plan_code,
                "status": "active",
                "is_active": True,
                "current_period_end": current_period_end,
                "created_at": now_iso,
                "updated_at": now_iso
            }).execute()
        
        logger.info(f"Subscription activated for account {account_id}: {plan_code} until {current_period_end}")
        return {"ok": True, "plan_code": plan_code, "expires_at": current_period_end, "duration_days": duration_days}
        
    except Exception as e:
        logger.error(f"Error activating subscription: {e}")
        return {"ok": False, "error": str(e)}


def get_user_subscription(account_id: str) -> Optional[Dict[str, Any]]:
    """Get user's current active subscription"""
    try:
        result = _sb().table("user_subscriptions") \
            .select("*") \
            .eq("account_id", account_id) \
            .eq("is_active", True) \
            .eq("status", "active") \
            .order("created_at", desc=True) \
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
    current_period_end = sub.get("current_period_end")
    if current_period_end:
        try:
            exp_dt = datetime.fromisoformat(current_period_end.replace('Z', '+00:00'))
            if exp_dt < datetime.now(timezone.utc):
                return False
        except:
            pass
    
    return True


def format_subscription_message(account_id: str) -> str:
    """Format subscription status message - shows actual credits"""
    subscription = get_user_subscription(account_id)
    
    if subscription and subscription.get("is_active"):
        plan_code = subscription.get("plan_code", "unknown")
        current_period_end = subscription.get("current_period_end", "")
        
        # Get plan details
        plan = validate_plan_code(plan_code)
        
        if plan:
            plan_name = plan["full_name"]
            credits = plan["credits"]
            monthly_credits = plan.get("monthly_credits", credits)
            daily_limit = plan.get("daily_limit", 0)
            billing_cycle = plan.get("billing_cycle", "monthly")
        else:
            plan_name = plan_code.replace("_", " ").title()
            credits = "?"
            monthly_credits = "?"
            daily_limit = "?"
            billing_cycle = "monthly"
        
        expiry_text = ""
        if current_period_end:
            try:
                dt = datetime.fromisoformat(current_period_end.replace('Z', '+00:00'))
                expiry_text = f"\n📅 Next billing: {dt.strftime('%b %d, %Y')}"
            except:
                pass
        
        # Format credit display based on billing cycle
        if billing_cycle == "monthly":
            credit_display = f"{credits} AI credits per month"
            access_text = f"You have {credits} AI credits to use this month."
        elif billing_cycle == "quarterly":
            credit_display = f"{credits} AI credits per quarter ({monthly_credits} per month)"
            access_text = f"You have {credits} AI credits to use over the next 3 months."
        else:  # yearly
            credit_display = f"{credits} AI credits per year ({monthly_credits} per month)"
            access_text = f"You have {credits} AI credits to use over the next year."
        
        return (f"📋 *YOUR SUBSCRIPTION*\n\n"
                f"✅ Plan: {plan_name}\n"
                f"🎯 Credits: {credit_display}\n"
                f"📊 Daily limit: {daily_limit} questions/day\n"
                f"{expiry_text}\n\n"
                f"{access_text}\n"
                f"🔄 Auto-renews {billing_cycle}\n\n"
                f"To cancel, contact support.")
    else:
        return ("📋 *NO ACTIVE SUBSCRIPTION*\n\n"
                "You are on the Free plan.\n"
                "🎯 Free: 10 AI credits\n\n"
                "Reply with 4 to see available plans and upgrade.")


def get_credit_balance_with_subscription(account_id: str, base_balance: int) -> Tuple[int, str]:
    """Get credit balance considering active subscription"""
    if has_active_subscription(account_id):
        sub = get_user_subscription(account_id)
        plan = validate_plan_code(sub.get("plan_code", "")) if sub else None
        if plan:
            return plan.get("credits", 0), "subscription"
        return base_balance, "subscription"
    return base_balance, "free"
