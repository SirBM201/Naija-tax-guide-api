# app/services/paystack_webhook_service.py
from __future__ import annotations

import os
import json
import hmac
import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.core.supabase_client import supabase


PAYSTACK_SECRET_KEY = (os.getenv("PAYSTACK_SECRET_KEY", "") or "").strip()


def _sb():
    return supabase() if callable(supabase) else supabase


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _verify_paystack_signature(raw_body: bytes, signature: str) -> bool:
    if not PAYSTACK_SECRET_KEY or not signature:
        return False
    digest = hmac.new(
        PAYSTACK_SECRET_KEY.encode("utf-8"),
        raw_body,
        hashlib.sha512,
    ).hexdigest()
    return hmac.compare_digest(digest, signature.strip())


def _safe_json_loads(raw_body: bytes) -> Dict[str, Any]:
    try:
        return json.loads(raw_body.decode("utf-8"))
    except Exception:
        return {}


def _extract_meta(data: Dict[str, Any]) -> Dict[str, Any]:
    meta = data.get("metadata")
    return meta if isinstance(meta, dict) else {}


def _get_nested(d: Dict[str, Any], *path: str) -> Optional[Any]:
    cur: Any = d
    for k in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _normalize_plan_code(plan_code: Optional[str]) -> Optional[str]:
    if not plan_code:
        return None
    return plan_code.strip().lower() or None


def _try_lookup_account_id_by_wa_phone(wa_phone: Optional[str]) -> Optional[str]:
    """
    Returns GLOBAL account identifier:
      ✅ accounts.account_id preferred
      ↪ fallback to accounts.id

    Failure exposer:
      - If schema mismatch, returns None (best-effort).
    """
    if not wa_phone:
        return None
    try:
        res = (
            _sb()
            .table("accounts")
            .select("id,account_id")
            .or_(f"wa_phone.eq.{wa_phone},phone.eq.{wa_phone},phone_e164.eq.{wa_phone}")
            .limit(1)
            .execute()
        )
        rows = res.data or []
        if not rows:
            return None
        row = rows[0] or {}
        return (row.get("account_id") or row.get("id"))
    except Exception:
        return None


def _upsert_paystack_payments(reference: str, payload: Dict[str, Any], meta: Dict[str, Any]) -> None:
    data = payload.get("data") or {}
    customer_email = _get_nested(data, "customer", "email") or meta.get("email") or None
    wa_phone = meta.get("wa_phone") or meta.get("phone") or None
    plan = meta.get("plan") or meta.get("plan_code") or None

    row = {
        "reference": reference,
        "wa_phone": wa_phone,
        "email": customer_email,
        "plan": plan,
        "amount_kobo": data.get("amount"),
        "currency": data.get("currency"),
        "status": data.get("status"),
        "gateway_response": data.get("gateway_response"),
        "raw": payload,
        "updated_at": _now_utc().isoformat(),
    }

    _sb().table("paystack_payments").upsert(row, on_conflict="reference").execute()


def _upsert_payments(reference: str, payload: Dict[str, Any], meta: Dict[str, Any], account_id: Optional[str]) -> None:
    data = payload.get("data") or {}
    wa_phone = meta.get("wa_phone") or meta.get("phone") or (data.get("customer") or {}).get("phone") or None
    email = (data.get("customer") or {}).get("email") or meta.get("email") or None
    plan_code = _normalize_plan_code(meta.get("plan_code") or meta.get("plan") or None)

    row = {
        "reference": reference,
        "wa_phone": wa_phone or "",
        "provider": "paystack",
        "plan": (meta.get("plan") or plan_code or "") if (meta.get("plan") or plan_code) else "",
        "amount_kobo": int(data.get("amount") or 0),
        "currency": (data.get("currency") or "NGN"),
        "status": (data.get("status") or "unknown"),
        "paid_at": data.get("paid_at"),
        "raw_event": payload,
        "email": email,
        "amount": None,
        "account_id": account_id,
        "provider_ref": reference,
        "raw": payload,
        "updated_at": _now_utc().isoformat(),
        "plan_code": plan_code,
    }

    _sb().table("payments").upsert(row, on_conflict="reference").execute()
