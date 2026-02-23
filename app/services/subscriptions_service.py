from __future__ import annotations

from typing import Any, Dict, Optional

from app.core.supabase_client import supabase

# If you already have this function, keep using it:
# def activate_subscription_now(account_id: str, plan_code: str, status: str = "active") -> Dict[str, Any]: ...


def _sb():
    return supabase() if callable(supabase) else supabase


def handle_payment_success(
    *,
    reference: str,
    event: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Webhook success handler.

    - Extracts account_id + plan_code from Paystack payload metadata
    - Activates subscription
    - Marks any transaction row as success (best-effort)
    """
    payload = (event or {}).get("data") or (event or {})
    metadata = payload.get("metadata") or {}

    account_id = (metadata.get("account_id") or "").strip()
    plan_code = (metadata.get("plan_code") or "").strip().lower()

    if not account_id or not plan_code:
        raise ValueError("missing account_id/plan_code in paystack metadata")

    # best-effort: mark transaction row
    try:
        _sb().table("paystack_transactions").update(
            {"status": "success", "paystack_status": "success", "raw": event or {}}
        ).eq("reference", reference).execute()
    except Exception:
        pass

    # Activate subscription (your existing logic)
    sub = activate_subscription_now(account_id=account_id, plan_code=plan_code, status="active")
    return {"ok": True, "subscription": sub, "reference": reference}


def handle_payment_failed(
    *,
    reference: str,
    event: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Webhook failure handler (best-effort transaction update).
    """
    try:
        _sb().table("paystack_transactions").update(
            {"status": "failed", "paystack_status": "failed", "raw": event or {}}
        ).eq("reference", reference).execute()
    except Exception:
        pass
    return {"ok": True, "reference": reference, "status": "failed"}
