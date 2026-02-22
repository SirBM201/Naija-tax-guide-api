# app/services/auth_context_service.py
from __future__ import annotations

from typing import Any, Dict, Optional

from app.core.supabase_client import supabase
from app.services.subscriptions_service import get_subscription_status


def get_auth_context(account_id: Optional[str]) -> Dict[str, Any]:
    """
    Returns a lightweight context object used by the frontend/web flows.

    Important: subscription status is sourced from subscriptions_service
    (single source of truth).
    """
    if not account_id:
        return {"ok": False, "error": "missing_account_id"}

    # Basic account profile (best effort)
    profile: Optional[Dict[str, Any]] = None
    try:
        res = (
            supabase.table("accounts")
            .select("account_id, provider, provider_user_id, display_name, phone, created_at")
            .eq("account_id", account_id)
            .limit(1)
            .execute()
        )
        rows = (res.data or []) if hasattr(res, "data") else []
        profile = rows[0] if rows else None
    except Exception:
        profile = None

    # Subscription status (source of truth)
    sub = get_subscription_status(account_id)

    return {
        "ok": True,
        "account_id": account_id,
        "profile": profile,
        "subscription": sub,
    }
