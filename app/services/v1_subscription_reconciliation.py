from __future__ import annotations

"""V1 subscription period reconciliation.

Legacy NTG subscription rows may retain an old ``expires_at`` even after a later
verified Paystack subscription payment.  This module keeps read paths consistent
and can repair that stale local period from the latest successful, applied
subscription transaction without charging or creating a new payment.
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from app.core.supabase_client import supabase

V1_SUBSCRIPTION_RECONCILIATION_VERSION = "2026-09-07-v2-payment-backed-period"

_PERIOD_FIELDS = (
    "current_period_end",
    "expires_at",
    "ends_at",
    "period_end",
    "grace_until",
    "trial_until",
)


def _sb():
    return supabase() if callable(supabase) else supabase


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


def _metadata(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


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


def _duration_days(plan_code: str) -> int:
    code = str(plan_code or "").strip().lower()
    if "yearly" in code or "annual" in code:
        return 365
    if "quarterly" in code:
        return 90
    return 30


def _latest_verified_subscription_payment(account_id: str) -> Optional[Dict[str, Any]]:
    try:
        res = (
            _sb().table("paystack_transactions").select("*")
            .eq("account_id", str(account_id).strip())
            .order("created_at", desc=True).limit(50).execute()
        )
        rows = [r for r in (getattr(res, "data", None) or []) if isinstance(r, dict)]
    except Exception:
        return None

    best = None
    best_dt = None
    for row in rows:
        status = str(row.get("status") or "").strip().lower()
        if status not in {"success", "paid", "applied", "completed"}:
            continue
        meta = _metadata(row.get("metadata"))
        kind = str(meta.get("type") or meta.get("purpose") or row.get("event_type") or "").strip().lower()
        plan_code = str(row.get("plan_code") or meta.get("plan_code") or "").strip().lower()
        if not plan_code or plan_code in {"free", "free_forever"}:
            continue
        if kind and kind not in {"subscription", "plan", "plan_purchase", "charge.success"} and not meta.get("applied_subscription"):
            continue
        paid_dt = _parse(row.get("paid_at") or meta.get("paid_at") or meta.get("applied_at") or row.get("updated_at") or row.get("created_at"))
        if paid_dt is not None and (best_dt is None or paid_dt > best_dt):
            best = row
            best_dt = paid_dt
    return best


def reconcile_subscription_row(account_id: str, row: Optional[Dict[str, Any]], *, persist: bool = True) -> Optional[Dict[str, Any]]:
    if not row:
        return row
    out = dict(row)
    current_end = _parse(latest_period_end(out))
    payment = _latest_verified_subscription_payment(account_id)
    if not payment:
        return out

    meta = _metadata(payment.get("metadata"))
    plan_code = str(payment.get("plan_code") or meta.get("plan_code") or out.get("plan_code") or "").strip().lower()
    paid_dt = _parse(payment.get("paid_at") or meta.get("paid_at") or meta.get("applied_at") or payment.get("updated_at") or payment.get("created_at"))
    if not paid_dt:
        return out

    explicit_end = _parse(meta.get("expires_at") or meta.get("current_period_end"))
    payment_end = explicit_end or (paid_dt + timedelta(days=_duration_days(plan_code)))
    if current_end is not None and payment_end <= current_end:
        return out

    end_iso = payment_end.isoformat()
    out.update({
        "plan_code": plan_code or out.get("plan_code"),
        "status": "active",
        "is_active": True,
        "current_period_end": end_iso,
        "expires_at": end_iso,
    })

    if persist:
        try:
            payload = {
                "plan_code": out.get("plan_code"),
                "status": "active",
                "is_active": True,
                "current_period_end": end_iso,
                "expires_at": end_iso,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            row_id = out.get("id")
            q = _sb().table("user_subscriptions").update(payload)
            if row_id:
                q = q.eq("id", row_id)
            else:
                q = q.eq("account_id", str(account_id).strip())
            q.execute()
        except Exception:
            # Read reconciliation must remain available even on a legacy schema.
            pass
    return out


def install_v1_subscription_reconciliation() -> None:
    try:
        from app.routes import billing
        billing._subscription_expiry = latest_period_end
        original_payload = billing._subscription_payload

        def reconciled_payload(account_id, sub, *args, **kwargs):
            return original_payload(account_id, reconcile_subscription_row(account_id, sub), *args, **kwargs)

        if not getattr(billing._subscription_payload, "_ntg_v1_reconciled", False):
            reconciled_payload._ntg_v1_reconciled = True
            billing._subscription_payload = reconciled_payload
    except Exception:
        pass

    try:
        from app.services import subscription_guard

        def _expiry_dt(sub):
            return subscription_guard._safe_dt(latest_period_end(sub))

        subscription_guard._expiry_dt = _expiry_dt
    except Exception:
        pass

    try:
        from app.services import account_entitlements_service as ent
        ent._subscription_expiry = latest_period_end
    except Exception:
        pass
