# app/services/paystack_webhook_service.py
from __future__ import annotations

"""PAYSTACK WEBHOOK SERVICE (CANONICAL)

Problem that caused FK 23503:
- Older code stored accounts.id as account_id in downstream tables.
- Your schema intends web_tokens.account_id to reference accounts.account_id.

✅ Canonical identity enforced here:
- When we resolve a user from Paystack metadata (phone/email), we ALWAYS return accounts.account_id.
- If accounts.account_id is NULL, we auto-repair it: account_id = id.

Also provides strong failure exposers.

"""

import os
import json
import hmac
import hashlib
from typing import Any, Dict, Optional

from app.core.supabase_client import supabase
from app.services.subscriptions_service import activate_subscription_now


def _sb():
    return supabase() if callable(supabase) else supabase


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or default).strip()


def _clip(s: str, n: int = 260) -> str:
    s = str(s or "")
    return s if len(s) <= n else s[:n] + "…"


def _has_column(table: str, col: str) -> bool:
    try:
        _sb().table(table).select(col).limit(1).execute()
        return True
    except Exception:
        return False


PAYSTACK_SECRET = _env("PAYSTACK_SECRET_KEY")


def verify_paystack_signature(raw_body: bytes, signature: str) -> bool:
    if not PAYSTACK_SECRET:
        # fail closed in prod
        return False
    computed = hmac.new(PAYSTACK_SECRET.encode("utf-8"), raw_body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(computed, signature or "")


def _repair_account_id_if_needed(row: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure canonical accounts.account_id exists."""
    account_id = str(row.get("account_id") or "").strip()
    row_id = str(row.get("id") or "").strip()

    if account_id:
        return {"ok": True, "account_id": account_id}

    if not row_id:
        return {
            "ok": False,
            "error": "account_id_missing",
            "root_cause": "accounts row has no id and no account_id",
            "fix": "Ensure accounts.id has uuid default and accounts.account_id exists.",
        }

    try:
        _sb().table("accounts").update({"account_id": row_id}).eq("id", row_id).execute()
        return {"ok": True, "account_id": row_id, "repaired": True}
    except Exception as e:
        return {
            "ok": False,
            "error": "account_id_repair_failed",
            "root_cause": f"{type(e).__name__}: {_clip(str(e))}",
            "fix": "Run SQL: update accounts set account_id=id where account_id is null; then UNIQUE index on account_id.",
            "details": {"row_id": row_id},
        }


def _try_lookup_account_id_by_wa_phone(phone_e164: str) -> Dict[str, Any]:
    """Lookup account by WA phone and return canonical accounts.account_id."""
    phone_e164 = (phone_e164 or "").strip()
    if not phone_e164:
        return {"ok": False, "error": "missing_phone"}

    # best effort normalize
    if not phone_e164.startswith("+") and phone_e164.isdigit():
        phone_e164 = "+" + phone_e164

    try:
        # prefer searching provider_user_id (WA uses phone as provider_user_id)
        q = (
            _sb()
            .table("accounts")
            .select("id,account_id,provider,provider_user_id")
            .eq("provider", "wa")
            .eq("provider_user_id", phone_e164)
            .limit(1)
            .execute()
        )
        rows = getattr(q, "data", None) or []
    except Exception as e:
        return {
            "ok": False,
            "error": "account_lookup_failed",
            "root_cause": f"{type(e).__name__}: {_clip(str(e))}",
            "fix": "Check accounts table and RLS permissions.",
        }

    if not rows:
        return {
            "ok": False,
            "error": "account_not_found",
            "root_cause": "no accounts row matched wa/provider_user_id",
            "fix": "Ensure the WhatsApp account exists in accounts table with provider='wa' and provider_user_id='+<e164>'.",
            "details": {"phone_e164": phone_e164},
        }

    return _repair_account_id_if_needed(rows[0] or {})


def process_paystack_webhook(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Entry point called by routes/paystack_webhook.py."""

    event = (payload or {}).get("event")
    data = (payload or {}).get("data") or {}

    if event not in {"charge.success", "subscription.create", "subscription.disable", "invoice.create", "invoice.update"}:
        return {"ok": True, "ignored": True, "event": event}

    # We care most about successful charges (subscription/payment)
    if event != "charge.success":
        return {"ok": True, "handled": True, "event": event}

    metadata = data.get("metadata") or {}

    # Your implementation: you likely attach phone in metadata
    phone = (metadata.get("phone") or metadata.get("phone_e164") or "").strip()

    if not phone:
        return {
            "ok": False,
            "error": "missing_metadata_phone",
            "root_cause": "paystack metadata missing phone/phone_e164",
            "fix": "When initializing Paystack transaction, include metadata: { phone_e164: '+234...' }.",
            "details": {"metadata_keys": sorted(list(metadata.keys()))},
        }

    acct = _try_lookup_account_id_by_wa_phone(phone)
    if not acct.get("ok"):
        return {
            "ok": False,
            "error": "account_resolve_failed",
            "root_cause": acct.get("root_cause") or acct.get("error"),
            "fix": acct.get("fix") or "Fix accounts lookup/identity mapping.",
            "details": acct.get("details") or {"phone": phone},
        }

    account_id = str(acct.get("account_id"))

    # activate subscription (expects canonical account_id)
    plan_code = (metadata.get("plan_code") or data.get("plan") or "").strip()
    if not plan_code:
        # allow your existing mapping logic elsewhere, but expose
        return {
            "ok": False,
            "error": "missing_plan_code",
            "root_cause": "plan_code not provided in metadata",
            "fix": "Include metadata.plan_code during Paystack initialization, or map Paystack plan -> internal plan code.",
            "details": {"metadata_keys": sorted(list(metadata.keys()))},
        }

    act = activate_subscription_now(account_id=account_id, plan_code=plan_code, event_payload=payload)
    if not act.get("ok"):
        return {
            "ok": False,
            "error": "subscription_activate_failed",
            "root_cause": act.get("root_cause") or act.get("error"),
            "fix": act.get("fix") or "Inspect subscriptions_service and DB constraints.",
            "details": act.get("details") or {"account_id": account_id, "plan_code": plan_code},
        }

    return {
        "ok": True,
        "event": event,
        "account_id": account_id,
        "plan_code": plan_code,
        "repaired_account_id": bool(acct.get("repaired")),
    }
