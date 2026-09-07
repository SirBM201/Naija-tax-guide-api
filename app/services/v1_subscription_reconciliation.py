from __future__ import annotations

"""V1 subscription read reconciliation.

Older NTG rows can contain both ``expires_at`` and ``current_period_end``.
A successful renewal may advance only one of those columns.  Reading the first
non-empty column can therefore make a renewed subscription appear expired.
For entitlement/display purposes the authoritative local period end is the
latest valid period timestamp present on the row.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional

V1_SUBSCRIPTION_RECONCILIATION_VERSION = "2026-09-07-v1-latest-period-end"

_PERIOD_FIELDS = (
    "current_period_end",
    "expires_at",
    "ends_at",
    "period_end",
    "grace_until",
    "trial_until",
)


def _parse(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def latest_period_end(row: Optional[Dict[str, Any]]) -> Optional[str]:
    row = row or {}
    candidates = []
    for field in _PERIOD_FIELDS:
        raw = row.get(field)
        parsed = _parse(raw)
        if parsed is not None:
            candidates.append((parsed, str(raw)))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def install_v1_subscription_reconciliation() -> None:
    # Website billing/workspace payloads.
    try:
        from app.routes import billing
        billing._subscription_expiry = latest_period_end
    except Exception:
        pass

    # Shared paid-entitlement guard used by web and channel flows.
    try:
        from app.services import subscription_guard

        def _expiry_dt(sub):
            return subscription_guard._safe_dt(latest_period_end(sub))

        subscription_guard._expiry_dt = _expiry_dt
    except Exception:
        pass
