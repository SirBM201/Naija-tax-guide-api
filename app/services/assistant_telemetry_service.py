from __future__ import annotations

"""V1 assistant usage telemetry, cost ceilings and lightweight abuse controls.

Deterministic guidance is never blocked. Paid-AI candidates are constrained
before inference by message-size, per-account RPM and a process-wide daily
estimated-cost ceiling. Telemetry persistence is best-effort and is not relied
upon for the hard budget guard.
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
_RESERVED_DAILY_COST_USD = 0.0


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
ASSISTANT_DAILY_COST_USD = _float_env("NTG_ASSISTANT_DAILY_COST_USD", 5.0)
ASSISTANT_ESTIMATED_AI_CALL_USD = _float_env("NTG_ASSISTANT_ESTIMATED_AI_CALL_USD", 0.01)


def _utc_day() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _reserve_daily_budget_locked() -> Dict[str, Any]:
    global _BUDGET_DAY, _RESERVED_DAILY_COST_USD

    today = _utc_day()
    if _BUDGET_DAY != today:
        _BUDGET_DAY = today
        _RESERVED_DAILY_COST_USD = 0.0

    estimate = ASSISTANT_ESTIMATED_AI_CALL_USD
    ceiling = ASSISTANT_DAILY_COST_USD

    # A zero ceiling deliberately disables paid AI. A zero estimate means the
    # configured provider is treated as no-cost for this guard.
    if ceiling <= 0.0 and estimate > 0.0:
        return {
            "ok": False,
            "error": "assistant_daily_budget_exhausted",
            "daily_cost_ceiling_usd": ceiling,
            "estimated_daily_cost_usd": round(_RESERVED_DAILY_COST_USD, 6),
        }

    projected = _RESERVED_DAILY_COST_USD + estimate
    if estimate > 0.0 and projected > ceiling + 1e-9:
        return {
            "ok": False,
            "error": "assistant_daily_budget_exhausted",
            "daily_cost_ceiling_usd": ceiling,
            "estimated_daily_cost_usd": round(_RESERVED_DAILY_COST_USD, 6),
        }

    _RESERVED_DAILY_COST_USD = projected
    return {
        "ok": True,
        "daily_cost_ceiling_usd": ceiling,
        "estimated_daily_cost_usd": round(_RESERVED_DAILY_COST_USD, 6),
        "reserved_call_cost_usd": estimate,
    }


def preflight_paid_ai(*, account_id: str, message: str) -> Dict[str, Any]:
    """Hard local guard before a request is allowed to reach paid inference."""
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

        budget = _reserve_daily_budget_locked()
        if not budget.get("ok"):
            return budget

        if ASSISTANT_AI_REQUESTS_PER_MINUTE > 0:
            _REQUESTS[key].append(now)

    return budget


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
            "cost_route": "budget_guard",
            "ai_called": False,
            "usage_charged": False,
            "credits_consumed": 0,
        },
    }
