# app/services/ask_guard.py
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from app.services.subscriptions_service import get_subscription_status


def require_subscription_or_error(
    account_id: Optional[str],
    channel: str = "web_ask",
) -> Tuple[bool, Dict[str, Any]]:
    """
    Returns (allowed, error_payload_if_blocked)
    """
    sub = get_subscription_status(account_id)

    if sub.get("active"):
        return True, {"ok": True, "subscription": sub}

    # Root-cause exposer (debug)
    payload = {
        "answer": "Please activate a plan to use NaijaTax Guide.",
        "error": "subscription_required",
        "meta": {
            "channel": channel,
            "debug": {
                "stage": "subscription_checked",
                "sub_reason": sub.get("reason"),
                "subscription_state": sub.get("state"),
            },
        },
        "subscription": sub,
        "ok": False,
    }
    return False, payload
