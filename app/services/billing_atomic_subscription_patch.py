from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.core.supabase_client import supabase
from app.services.credits_service import add_plan_credits_for_payment
from app.services.plans_service import get_plan

logger = logging.getLogger(__name__)
PATCH_VERSION = "2026-09-07-v1-atomic-billing-subscription"


def _sb():
    return supabase() if callable(supabase) else supabase


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _lower(value: Any) -> str:
    return _clean(value).lower()


def _duration_days(plan_code: str) -> int:
    plan = get_plan(plan_code) or {}
    try:
        days = int(plan.get("duration_days") or 0)
    except Exception:
        days = 0
    if days > 0:
        return days
    if "yearly" in plan_code:
        return 365
    if "quarterly" in plan_code:
        return 90
    return 30


def atomic_activate_subscription(
    account_id: str,
    plan_code: str,
    reference: str,
    *,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Fulfill a verified website subscription payment exactly once.

    Subscription-period mutation is owned by the database RPC. Included credits
    are separately idempotent using the same Paystack reference.
    """
    account_id = _clean(account_id)
    plan_code = _lower(plan_code)
    reference = _clean(reference)
    metadata = dict(metadata or {})

    if not account_id:
        return {"ok": False, "error": "account_id_required", "patch_version": PATCH_VERSION}
    if not plan_code:
        return {"ok": False, "error": "plan_code_required", "patch_version": PATCH_VERSION}
    if not reference:
        return {"ok": False, "error": "reference_required", "patch_version": PATCH_VERSION}
    if not get_plan(plan_code):
        return {"ok": False, "error": "plan_not_found", "plan_code": plan_code, "patch_version": PATCH_VERSION}

    duration_days = _duration_days(plan_code)
    try:
        response = _sb().rpc(
            "ntg_fulfill_subscription_payment",
            {
                "p_account_id": account_id,
                "p_plan_code": plan_code,
                "p_reference": reference,
                "p_duration_days": duration_days,
            },
        ).execute()
        fulfillment = getattr(response, "data", None)
        if isinstance(fulfillment, list):
            fulfillment = fulfillment[0] if fulfillment else None
        if not isinstance(fulfillment, dict) or not fulfillment.get("ok"):
            return {
                "ok": False,
                "error": "subscription_fulfillment_failed",
                "fulfillment": fulfillment,
                "patch_version": PATCH_VERSION,
            }
    except Exception as exc:
        logger.exception("Atomic website subscription RPC failed")
        return {
            "ok": False,
            "error": "subscription_fulfillment_exception",
            "root_cause": f"{type(exc).__name__}: {exc}",
            "patch_version": PATCH_VERSION,
        }

    try:
        credit_result = add_plan_credits_for_payment(
            account_id,
            plan_code,
            reference,
            source="web_subscription_payment",
        )
        if not isinstance(credit_result, dict):
            credit_result = {"ok": False, "error": "invalid_credit_result", "raw": str(credit_result)}
    except Exception as exc:
        logger.exception("Website subscription credit fulfillment failed")
        credit_result = {
            "ok": False,
            "error": "credit_fulfillment_exception",
            "root_cause": f"{type(exc).__name__}: {exc}",
        }

    period_end = fulfillment.get("period_end")
    return {
        "ok": True,
        "account_id": account_id,
        "plan_code": plan_code,
        "reference": reference,
        "expires_at": period_end,
        "current_period_end": period_end,
        "duration_days": duration_days,
        "applied": bool(fulfillment.get("applied")),
        "duplicate": bool(fulfillment.get("duplicate")),
        "fulfillment": fulfillment,
        "credits": credit_result,
        "credit_balance": credit_result.get("balance"),
        "metadata": metadata,
        "patch_version": PATCH_VERSION,
    }


def install() -> Dict[str, Any]:
    """Replace billing._activate_subscription without rewriting billing.py."""
    try:
        from app.routes import billing

        current = getattr(billing, "_activate_subscription", None)
        if getattr(current, "_ntg_atomic_subscription_patch", False):
            return {"ok": True, "installed": True, "already_installed": True, "version": PATCH_VERSION}

        setattr(atomic_activate_subscription, "_ntg_atomic_subscription_patch", True)
        billing._activate_subscription = atomic_activate_subscription
        logger.info("Installed NTG atomic website subscription patch %s", PATCH_VERSION)
        return {"ok": True, "installed": True, "version": PATCH_VERSION}
    except Exception as exc:
        logger.exception("Failed to install atomic website subscription patch")
        return {"ok": False, "installed": False, "error": f"{type(exc).__name__}: {exc}", "version": PATCH_VERSION}
