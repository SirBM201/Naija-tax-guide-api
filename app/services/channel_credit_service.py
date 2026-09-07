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
SERVICE_VERSION = "2026-09-07-v1-paid-subscriber-topups"

CREDIT_PACKAGES = {
    1: {"credits": 10, "amount_ngn": 500, "amount_kobo": 50000, "description": "10 AI Credits"},
    2: {"credits": 50, "amount_ngn": 2000, "amount_kobo": 200000, "description": "50 AI Credits"},
    3: {"credits": 100, "amount_ngn": 3500, "amount_kobo": 350000, "description": "100 AI Credits"},
    4: {"credits": 500, "amount_ngn": 15000, "amount_kobo": 1500000, "description": "500 AI Credits"},
}


def _sb():
    return supabase() if callable(supabase) else supabase


def get_credit_balance(account_id: str) -> int:
    try:
        result = _sb().table("ai_credit_balances").select("balance").eq("account_id", account_id).limit(1).execute()
        if result.data:
            return result.data[0].get("balance", 0)
        return 0
    except Exception as e:
        logger.error(f"Error getting credit balance: {e}")
        return 0


def get_credit_packages_menu() -> str:
    return (
        "💎 *Buy AI Credits*\n\nReply with the package number:\n\n"
        "1️⃣ - 10 credits - ₦500\n2️⃣ - 50 credits - ₦2,000\n"
        "3️⃣ - 100 credits - ₦3,500\n4️⃣ - 500 credits - ₦15,000\n\nEnter 0 to cancel."
    )


def validate_package_number(package_num: int) -> Optional[Dict[str, Any]]:
    return CREDIT_PACKAGES.get(package_num)


def get_or_create_account_id(channel_type: str, provider_user_id: str) -> str:
    try:
        result = _sb().table("accounts").select("account_id").eq("provider", channel_type).eq("provider_user_id", provider_user_id).limit(1).execute()
        if result.data:
            account_id = result.data[0].get("account_id")
            if account_id:
                return account_id
        new_account_id = str(uuid.uuid4())
        _sb().table("accounts").insert({"id": new_account_id, "account_id": new_account_id, "provider": channel_type, "provider_user_id": provider_user_id, "created_at": datetime.now(timezone.utc).isoformat()}).execute()
        _sb().table("ai_credit_balances").insert({"account_id": new_account_id, "balance": 0, "updated_at": datetime.now(timezone.utc).isoformat()}).execute()
        return new_account_id
    except Exception as e:
        logger.error(f"Error getting/creating account: {e}")
        return provider_user_id


def _require_paid_topup_entitlement(account_id: str) -> Dict[str, Any]:
    """Top-ups are an add-on for active paid subscribers only."""
    try:
        from app.services.account_entitlements_service import get_account_entitlements
        ent = get_account_entitlements(account_id)
    except Exception as exc:
        logger.error("Could not resolve top-up entitlement account=%s: %s", account_id, exc)
        return {"ok": False, "error": "topup_entitlement_unavailable"}

    plan_code = str((ent or {}).get("plan_code") or "").strip().lower()
    subscription = (ent or {}).get("subscription")
    if not (ent or {}).get("ok") or not subscription or plan_code in {"", "free", "free_forever"}:
        return {
            "ok": False,
            "error": "active_paid_plan_required",
            "message": "Credit top-ups are available only to active paid subscribers. Please subscribe to a paid plan first.",
        }
    return {"ok": True, "plan_code": plan_code}


def create_credit_payment(account_id: str, package_num: int, channel_type: str, provider_user_id: str) -> Dict[str, Any]:
    entitlement = _require_paid_topup_entitlement(account_id)
    if not entitlement.get("ok"):
        return entitlement

    package = CREDIT_PACKAGES.get(package_num)
    if not package:
        return {"ok": False, "error": "invalid_package", "message": "Invalid package number. Please select 1-4."}
    reference = f"CREDIT_{package['credits']}_{uuid.uuid4().hex[:8]}"
    amount_kobo = package["amount_kobo"]
    credits = package["credits"]
    amount_ngn = package["amount_ngn"]
    try:
        _sb().table("paystack_transactions").insert({"reference": reference, "account_id": account_id, "amount": amount_kobo, "currency": PAYSTACK_CURRENCY or "NGN", "status": "pending", "plan_code": f"credits_{credits}", "created_at": datetime.now(timezone.utc).isoformat(), "metadata": {"account_id": account_id, "credits": credits, "package": package_num, "type": "credit_purchase", "channel_type": channel_type, "provider_user_id": provider_user_id, "amount_ngn": amount_ngn}}).execute()
    except Exception as e:
        logger.error(f"Error storing transaction: {e}")
    base_url = os.getenv("PUBLIC_BACKEND_BASE_URL", "https://incredible-nonie-bmsconcept-37359733.koyeb.app")
    callback_url = f"{base_url}/api/channel/payment/return?channel_type={channel_type}&provider_user_id={provider_user_id}&account_id={account_id}"
    try:
        result = initialize_transaction(amount_kobo=amount_kobo, email=None, reference=reference, metadata={"account_id": account_id, "credits": credits, "package": package_num, "type": "credit_purchase", "channel_type": channel_type, "provider_user_id": provider_user_id, "amount_ngn": amount_ngn}, callback_url=callback_url)
        if result.get("status") and result.get("data", {}).get("authorization_url"):
            return {"ok": True, "payment_link": result["data"]["authorization_url"], "reference": reference, "amount_ngn": amount_ngn, "credits": credits, "message": f"💰 *Payment Link*\n\nClick to pay ₦{amount_ngn:,} for {credits} AI credits:\n\n{result['data']['authorization_url']}\n\n✅ After payment, your credits will be added automatically.\n\n💡 No email needed - we'll identify you via your linked channel."}
        error_msg = result.get("message", "Payment initialization failed")
        return {"ok": False, "error": "payment_link_failed", "message": f"Could not generate payment link: {error_msg}\n\nPlease try again later."}
    except Exception as e:
        logger.error(f"Error creating payment link: {e}")
        return {"ok": False, "error": str(e), "message": "Payment service error. Please try again later."}


def add_credits_to_account(account_id: str, credits: int, reference: str) -> bool:
    """Atomically fulfill a verified Paystack credit purchase.

    Requires migration 20260907_paystack_atomic_credit_fulfillment.sql.
    Paid subscribers intentionally receive purchased add-on credits.  The
    purchase-creation path enforces the paid-plan requirement; fulfillment is
    reference-idempotent and must not silently discard a verified purchase.
    """
    account_id = str(account_id or "").strip()
    reference = str(reference or "").strip()
    try:
        credits = int(credits)
    except Exception:
        credits = 0
    if not account_id or not reference or credits <= 0:
        logger.error("Invalid atomic credit fulfillment arguments")
        return False

    try:
        response = _sb().rpc("ntg_fulfill_credit_purchase", {"p_account_id": account_id, "p_credits": credits, "p_reference": reference}).execute()
        data = getattr(response, "data", None)
        result = data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else data
        if not isinstance(result, dict) or result.get("ok") is not True:
            logger.error("Atomic credit fulfillment rejected account=%s reference=%s result=%s", account_id, reference, result)
            return False
        if result.get("duplicate"):
            logger.info("Duplicate Paystack fulfillment safely ignored reference=%s", reference)
        else:
            logger.info("Atomic credit fulfillment complete account=%s reference=%s credits=%s balance=%s", account_id, reference, credits, result.get("balance"))
        return True
    except Exception as e:
        logger.error("Atomic credit fulfillment RPC failed account=%s reference=%s: %s", account_id, reference, e)
        return False


def format_balance_message(balance: int) -> str:
    if balance == 0:
        return "💎 *AI Credits Balance*\n\nYou have *0 credits* remaining.\n\nEach credit = 1 AI tax question.\n\nTo buy credits, reply with 6."
    return f"💎 *AI Credits Balance*\n\nYou have *{balance} credits* remaining.\n\nEach credit = 1 AI tax question.\n\nTo buy more credits, reply with 6."


def get_user_email_status(account_id: str) -> Dict[str, Any]:
    try:
        result = _sb().table("accounts").select("email").eq("account_id", account_id).limit(1).execute()
        if result.data and result.data[0].get("email"):
            return {"has_email": True, "email": result.data[0]["email"]}
        return {"has_email": False, "email": None}
    except Exception as e:
        logger.error(f"Error checking email status: {e}")
        return {"has_email": False, "email": None}
