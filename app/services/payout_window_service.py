from __future__ import annotations

"""Referral payout-window policy for NTG V1.

Approved V1 policy: referral payouts are processed/requested on the 15th and
30th of each month. February and other months without a 30th use the final
calendar day as the second window so an earned balance is never stranded.

The policy uses UTC consistently with the payout ledger timestamps.
"""

import calendar
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def payout_window_enforced() -> bool:
    return _truthy(os.getenv("REFERRAL_PAYOUT_WINDOW_ENFORCED", "1"))


def payout_window_status(now: Optional[datetime] = None) -> Dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    else:
        current = current.astimezone(timezone.utc)

    last_day = calendar.monthrange(current.year, current.month)[1]
    second_day = min(30, last_day)
    allowed_days = tuple(dict.fromkeys((15, second_day)))
    open_now = current.day in allowed_days

    if open_now:
        next_day = current.day
        next_month = current.month
        next_year = current.year
    elif current.day < 15:
        next_day, next_month, next_year = 15, current.month, current.year
    elif current.day < second_day:
        next_day, next_month, next_year = second_day, current.month, current.year
    else:
        if current.month == 12:
            next_month, next_year = 1, current.year + 1
        else:
            next_month, next_year = current.month + 1, current.year
        next_day = 15

    next_window = datetime(next_year, next_month, next_day, tzinfo=timezone.utc)
    return {
        "enforced": payout_window_enforced(),
        "open": open_now or not payout_window_enforced(),
        "policy": "15th_and_30th",
        "timezone": "UTC",
        "allowed_days": list(allowed_days),
        "current_date": current.date().isoformat(),
        "next_window_date": next_window.date().isoformat(),
    }


def enforce_payout_window(now: Optional[datetime] = None) -> Dict[str, Any]:
    status = payout_window_status(now)
    if not status["open"]:
        raise ValueError(
            "Referral payout requests are accepted only on the 15th and 30th "
            f"payout windows. Next window: {status['next_window_date']}."
        )
    return status
