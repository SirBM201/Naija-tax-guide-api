from __future__ import annotations

"""V1 assistant usage telemetry and lightweight abuse controls.

Individual plan/credit entitlements remain authoritative for paid AI access.
This module enforces message-size and per-account request-rate controls, while
app-wide estimated spend thresholds are monitoring signals only and never deny
a valid user request. Deterministic guidance is never blocked here.
"""

import logging
import os
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, Dict

from app.core.supabase_client import supabase

_LOCK = threading.Lock()
_REQUESTS: dict[str, deque[float]] = defaultdict(deque)
_BUDGET_DAY = ""
_ESTIMATED_DAILY_COST_USD = 0.0


def _int_env(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, str(default))))
    except Exception:
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.getenv(name, str(default))))
    except Exception:
        return default


ASSISTANT_AI_REQUESTS_PER_MINUTE = _int_env("NTG_ASSISTANT_AI_REQUESTS_PER_MINUTE", 12)
ASSISTANT_MAX_MESSAGE_CHARS = _int_env("NTG_ASSISTANT_MAX_MESSAGE_CHARS", 6000)
# Backward-compatible env name. This is now an alert threshold, not a hard cap.
ASSISTANT_DAILY_COST_ALERT_USD = _float_env(
    "NTG_ASSISTANT_DAILY_COST_ALERT_USD",
    _float_env("NTG_ASSISTANT_DAILY_COST_USD", 5.0),
)
ASSISTANT_ESTIMATED_AI_CALL_USD = _float_env("NTG_ASSISTANT_ESTIMATED_AI_CALL_USD", 0.01)


def _utc_day() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _record_estimated_spend_locked() -> Dict[str, Any]:
    """Record estimated app-wide spend without blocking entitled customers."""
    global _BUDGET_DAY, _ESTIMATED_DAILY_COST_USD

    today = _utc_day()
    if _BUDGET_DAY != today:
        _BUDGET_DAY = today
        _ESTIMATED_DAILY_COST_USD = 0.0

    estimate = ASSISTANT_ESTIMATED_AI_CALL_USD
    _ESTIMATED_DAILY_COST_USD += estimate
    threshold = ASSISTANT_DAILY_COST_ALERT_USD
    alert = bool(threshold > 0.0 and _ESTIMATED_DAILY_COST_USD >= threshold)

    return {
        "ok": True,
        "estimated_daily_cost_usd": round(_ESTIMATED_DAILY_COST_USD, 6),
        "estimated_call_cost_usd": estimate,
        "daily_cost_alert_threshold_usd": threshold,
        "daily_cost_alert": alert,
        "budget_policy": "monitor_only",
    }


def preflight_paid_ai(*, account_id: str, message: str) -> Dict[str, Any]:
    """Per-user abuse guard plus non-blocking app-wide spend monitoring."""
    if ASSISTANT_MAX_MESSAGE_CHARS and len(message) > ASSISTANT_MAX_MESSAGE_CHARS:
        return {"ok": False, "error": "assistant_message_too_long", "retryable": True}

    now = time.time()
    cutoff = now - 60.0
    key = str(account_id or "anonymous")

    with _LOCK:
        if ASSISTANT_AI_REQUESTS_PER_MINUTE > 0:
            bucket = _REQUESTS[key]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= ASSISTANT_AI_REQUESTS_PER_MINUTE:
                return {"ok": False, "error": "assistant_rate_limited", "retry_after_seconds": 60}

        spend = _record_estimated_spend_locked()

        if ASSISTANT_AI_REQUESTS_PER_MINUTE > 0:
            _REQUESTS[key].append(now)

    if spend.get("daily_cost_alert"):
        logging.warning(
            "NTG assistant estimated daily AI spend reached monitoring threshold: estimated=%s threshold=%s",
            spend.get("estimated_daily_cost_usd"),
            spend.get("daily_cost_alert_threshold_usd"),
        )

    return spend


def record_assistant_event(
    *,
    account_id: str,
    channel: str,
    route: str,
    result: Dict[str, Any],
) -> None:
    """Persist minimal non-content telemetry when the optional table exists."""
    try:
        meta = dict(result.get("meta") or {})
        payload = {
            "account_id": account_id or None,
            "channel": channel,
            "route": route,
            "assistant_version": meta.get("assistant_version"),
            "cost_route": meta.get("cost_route"),
            "ai_called": bool(meta.get("ai_called") is True),
            "usage_charged": bool(meta.get("usage_charged") is True),
            "credits_consumed": int(meta.get("credits_consumed") or 0),
            "estimated_cost_usd": ASSISTANT_ESTIMATED_AI_CALL_USD if meta.get("ai_called") is True else 0.0,
            "result_ok": bool(result.get("ok") is True),
            "error_code": str(result.get("error") or "")[:120] or None,
        }
        supabase.table("assistant_usage_events").insert(payload).execute()
    except Exception:
        logging.debug("Assistant telemetry unavailable; continuing without persistence", exc_info=True)


def budget_fallback(*, account_id: str, channel: str) -> Dict[str, Any]:
    """Compatibility fallback; normal spend thresholds no longer invoke it."""
    return {
        "ok": False,
        "error": "assistant_budget_unavailable",
        "message": (
            "Paid AI guidance is temporarily unavailable. You can still use free calculators, "
            "approved database guidance, deadlines, quiz, plan/credit and account help."
        ),
        "source": "guided_assistant",
        "mode": "deterministic_fallback",
        "next_action": "Use a free Naija Tax Guide workflow or try the tax question again later.",
        "meta": {
            "account_id": account_id,
            "channel": channel,
            "cost_route": "emergency_guard",
            "ai_called": False,
            "usage_charged": False,
            "credits_consumed": 0,
        },
    }
