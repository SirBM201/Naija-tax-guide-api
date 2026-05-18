# app/routes/whatsapp.py
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import requests
from flask import Blueprint, jsonify, request

from app.core.supabase_client import supabase
from app.services.ask_service import ask_guarded

try:
    from app.services.paystack_service import create_reference, initialize_transaction
except Exception:  # pragma: no cover
    create_reference = None  # type: ignore
    initialize_transaction = None  # type: ignore


bp = Blueprint("whatsapp", __name__)

WHATSAPP_FLOW_VERSION = "2026-05-18-v4-webhook-405-fix"


# =============================================================================
# Data configuration
# =============================================================================

PLAN_OPTIONS: Dict[str, Dict[str, Any]] = {
    "S1": {
        "code": "S1",
        "plan_code": "starter_monthly",
        "name": "Starter Monthly",
        "price": 5000,
        "credits": 100,
        "cycle": "monthly",
        "family": "starter",
        "aliases": [
            "s1",
            "starter",
            "starter monthly",
            "starter month",
            "monthly starter",
            "starter plan",
            "5000",
            "5,000",
            "₦5000",
            "₦5,000",
            "100 credits",
            "starter 100",
        ],
    },
    "S2": {
        "code": "S2",
        "plan_code": "starter_quarterly",
        "name": "Starter Quarterly",
        "price": 14000,
        "credits": 300,
        "cycle": "quarterly",
        "family": "starter",
        "aliases": [
            "s2",
            "starter quarterly",
            "starter quarter",
            "quarterly starter",
            "14000",
            "14,000",
            "₦14000",
            "₦14,000",
            "300 credits starter",
            "starter 300",
        ],
    },
    "S3": {
        "code": "S3",
        "plan_code": "starter_yearly",
        "name": "Starter Yearly",
        "price": 51000,
        "credits": 1200,
        "cycle": "yearly",
        "family": "starter",
        "aliases": [
            "s3",
            "starter yearly",
            "starter annual",
            "yearly starter",
            "51000",
            "51,000",
            "₦51000",
            "₦51,000",
            "1200 credits",
            "starter 1200",
        ],
    },
    "P1": {
        "code": "P1",
        "plan_code": "professional_monthly",
        "name": "Professional Monthly",
        "price": 12000,
        "credits": 300,
        "cycle": "monthly",
        "family": "professional",
        "aliases": [
            "p1",
            "professional",
            "pro",
            "professional monthly",
            "pro monthly",
            "monthly professional",
            "12000",
            "12,000",
            "₦12000",
            "₦12,000",
            "300 credits professional",
            "pro 300",
        ],
    },
    "P2": {
        "code": "P2",
        "plan_code": "professional_quarterly",
        "name": "Professional Quarterly",
        "price": 33600,
        "credits": 900,
        "cycle": "quarterly",
        "family": "professional",
        "aliases": [
            "p2",
            "professional quarterly",
            "pro quarterly",
            "quarterly professional",
            "33600",
            "33,600",
            "₦33600",
            "₦33,600",
            "900 credits",
            "pro 900",
        ],
    },
    "P3": {
        "code": "P3",
        "plan_code": "professional_yearly",
        "name": "Professional Yearly",
        "price": 122400,
        "credits": 3600,
        "cycle": "yearly",
        "family": "professional",
        "aliases": [
            "p3",
            "professional yearly",
            "pro yearly",
            "yearly professional",
            "122400",
            "122,400",
            "₦122400",
            "₦122,400",
            "3600 credits",
            "pro 3600",
        ],
    },
    "B1": {
        "code": "B1",
        "plan_code": "business_monthly",
        "name": "Business Monthly",
        "price": 25000,
        "credits": 800,
        "cycle": "monthly",
        "family": "business",
        "aliases": [
            "b1",
            "business",
            "business monthly",
            "monthly business",
            "25000",
            "25,000",
            "₦25000",
            "₦25,000",
            "800 credits",
            "business 800",
        ],
    },
    "B2": {
        "code": "B2",
        "plan_code": "business_quarterly",
        "name": "Business Quarterly",
        "price": 70000,
        "credits": 2400,
        "cycle": "quarterly",
        "family": "business",
        "aliases": [
            "b2",
            "business quarterly",
            "quarterly business",
            "70000",
            "70,000",
            "₦70000",
            "₦70,000",
            "2400 credits",
            "business 2400",
        ],
    },
    "B3": {
        "code": "B3",
        "plan_code": "business_yearly",
        "name": "Business Yearly",
        "price": 255000,
        "credits": 9600,
        "cycle": "yearly",
        "family": "business",
        "aliases": [
            "b3",
            "business yearly",
            "business annual",
            "yearly business",
            "255000",
            "255,000",
            "₦255000",
            "₦255,000",
            "9600 credits",
            "business 9600",
        ],
    },
}

TOPUP_OPTIONS: Dict[str, Dict[str, Any]] = {
    "T10": {
        "code": "T10",
        "name": "Starter Add-on",
        "credits": 10,
        "price": 500,
        "aliases": ["t10", "10 credits", "10 extra credits", "500", "₦500", "buy 10", "topup 10"],
    },
    "T50": {
        "code": "T50",
        "name": "Smart Add-on",
        "credits": 50,
        "price": 2000,
        "aliases": ["t50", "50 credits", "50 extra credits", "2000", "2,000", "₦2000", "₦2,000", "topup 50"],
    },
    "T100": {
        "code": "T100",
        "name": "Growth Add-on",
        "credits": 100,
        "price": 3500,
        "aliases": ["t100", "100 extra credits", "100 topup", "3500", "3,500", "₦3500", "₦3,500", "topup 100"],
    },
    "T500": {
        "code": "T500",
        "name": "Business Add-on",
        "credits": 500,
        "price": 15000,
        "aliases": ["t500", "500 credits", "500 extra credits", "15000", "15,000", "₦15000", "₦15,000", "topup 500"],
    },
}

TOOL_OPTIONS: Dict[str, Dict[str, Any]] = {
    "F1": {"code": "F1", "action": "calculator_menu", "name": "Tax calculators", "aliases": ["f1", "calculator", "calculators", "tax calculator", "tax calculators", "calc"]},
    "F2": {"code": "F2", "action": "paye_guide", "name": "PAYE filing guide", "aliases": ["f2", "paye guide", "paye filing", "paye filing guide", "employee tax guide"]},
    "F3": {"code": "F3", "action": "vat_guide", "name": "VAT filing guide", "aliases": ["f3", "vat guide", "vat filing", "vat filing guide"]},
    "F4": {"code": "F4", "action": "cit_guide", "name": "CIT filing guide", "aliases": ["f4", "cit guide", "company tax guide", "company income tax guide", "cit filing"]},
    "F5": {"code": "F5", "action": "wht_guide", "name": "WHT guide", "aliases": ["f5", "wht guide", "withholding tax guide", "withholding guide"]},
    "F6": {"code": "F6", "action": "deadlines", "name": "Tax deadlines/calendar", "aliases": ["f6", "deadline", "deadlines", "calendar", "tax calendar", "due dates"]},
    "F7": {"code": "F7", "action": "filing_checklist", "name": "Filing checklist", "aliases": ["f7", "checklist", "filing checklist", "tax checklist"]},
    "F8": {"code": "F8", "action": "main_menu", "name": "Back to main menu", "aliases": ["f8", "main", "main menu", "menu"]},
}

CALC_OPTIONS: Dict[str, Dict[str, Any]] = {
    "C1": {"code": "C1", "action": "paye_calc", "name": "PAYE calculator", "aliases": ["c1", "paye calculator", "paye calc", "salary tax", "employee tax"]},
    "C2": {"code": "C2", "action": "cit_calc", "name": "Company Income Tax calculator", "aliases": ["c2", "cit calculator", "cit calc", "company income tax calculator", "company tax calculator"]},
    "C3": {"code": "C3", "action": "vat_calc", "name": "VAT calculator", "aliases": ["c3", "vat calculator", "vat calc"]},
    "C4": {"code": "C4", "action": "wht_calc", "name": "Withholding Tax calculator", "aliases": ["c4", "wht calculator", "wht calc", "withholding tax calculator"]},
    "C5": {"code": "C5", "action": "salary_compare", "name": "Salary/net pay comparison", "aliases": ["c5", "salary compare", "compare salary", "net pay", "salary comparison"]},
    "C6": {"code": "C6", "action": "tax_quiz", "name": "Tax quiz", "aliases": ["c6", "quiz", "tax quiz", "question quiz"]},
    "C7": {"code": "C7", "action": "deadlines", "name": "Tax calendar/deadlines", "aliases": ["c7", "tax calendar", "deadlines", "deadline"]},
    "C8": {"code": "C8", "action": "tools_menu", "name": "Back to Tax Tools", "aliases": ["c8", "back to tools", "tools"]},
}


# =============================================================================
# Generic helpers
# =============================================================================

def _sb():
    return supabase() if callable(supabase) else supabase


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _lower(value: Any) -> str:
    return _clean(value).lower()


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _clip(value: Any, limit: int = 700) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit] + "...<truncated>"


def _normalize_phone(value: Any) -> str:
    text = re.sub(r"\D+", "", _clean(value))
    if text.startswith("00"):
        text = text[2:]
    return text


def _display_phone(value: Any) -> str:
    phone = _normalize_phone(value)
    return f"+{phone}" if phone else ""


def _safe_json() -> Dict[str, Any]:
    data = request.get_json(silent=True) or {}
    return data if isinstance(data, dict) else {}


def _debug_enabled() -> bool:
    return _truthy(os.getenv("DEBUG_WHATSAPP")) or _truthy(os.getenv("DEBUG_AI"))


def _base_url() -> str:
    return _clean(os.getenv("APP_BASE_URL") or os.getenv("FRONTEND_BASE_URL") or "https://www.naijataxguides.com").rstrip("/")


def _whatsapp_bot_phone() -> str:
    """
    Real WhatsApp bot/business chat number in international format.

    Required Koyeb env:
    WHATSAPP_BOT_PHONE_NUMBER=234XXXXXXXXXX

    Do not use WHATSAPP_PHONE_NUMBER_ID here; that is Meta's internal ID.
    """
    return _normalize_phone(
        os.getenv("WHATSAPP_BOT_PHONE_NUMBER")
        or os.getenv("META_WHATSAPP_BOT_PHONE")
        or os.getenv("WHATSAPP_DISPLAY_PHONE_NUMBER")
        or os.getenv("META_WHATSAPP_DISPLAY_PHONE_NUMBER")
    )


def _whatsapp_return_url(customer_wa_id: str, message: str) -> str:
    """
    Paystack callback for WhatsApp-originated payments.

    It opens the BOT chat, while the customer phone stays in metadata for
    activation and notification.
    """
    bot_phone = _whatsapp_bot_phone()
    safe_message = quote(_clean(message)[:900])

    if not bot_phone:
        return f"{_base_url()}/billing/success?source=whatsapp&missing_bot_phone=1"

    return f"https://wa.me/{bot_phone}?text={safe_message}"


def _normalize_text(value: Any) -> str:
    text = _lower(value)
    text = text.replace("₦", " ")
    text = re.sub(r"[,]+", "", text)
    text = re.sub(r"[^a-z0-9\s]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_amounts(value: Any) -> List[int]:
    text = _clean(value).replace(",", "")
    amounts: List[int] = []
    for match in re.finditer(r"(?:₦\s*)?(\d{2,9})", text):
        try:
            amounts.append(int(match.group(1)))
        except Exception:
            pass
    return amounts


def _contains_credit_phrase(value: Any) -> bool:
    text = _normalize_text(value)
    return "credit" in text or "credits" in text


def _extract_payment_reference(value: Any) -> str:
    text = _clean(value)
    match = re.search(r"\b(NTG(?:-WA|-WA-TOPUP|-TOPUP)?-[A-Za-z0-9]+)\b", text, flags=re.I)
    return match.group(1) if match else ""



def _is_valid_email(value: Any) -> bool:
    text = _lower(value)
    return bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", text))


def _is_placeholder_email(value: Any) -> bool:
    email = _lower(value)
    return not email or email.startswith("user_") or email.endswith("@naijataxguides.com")


def _account_email(account: Optional[Dict[str, Any]]) -> str:
    return _clean((account or {}).get("email"))


def _ensure_email_or_prompt(
    *,
    wa_id: str,
    account: Optional[Dict[str, Any]],
    pending_action: str,
    data: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    email = _account_email(account)
    if email and not _is_placeholder_email(email):
        return None

    _set_session_state(wa_id, context="collect_email", pending_action=pending_action, data=data)
    body = (
        "Please enter your email address to continue with payment.\n\n"
        "Example: name@email.com\n\n"
        "This email will be used for your Paystack receipt and account record."
    )
    return {"ok": True, "handled": "collect_email", "send_result": _send_whatsapp_text(wa_id, body)}


def _query_one(table: str, select_cols: str = "*", **eq_filters: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    try:
        q = _sb().table(table).select(select_cols)
        for col, val in eq_filters.items():
            if val is not None and _clean(val):
                q = q.eq(col, val)
        res = q.limit(1).execute()
        rows = getattr(res, "data", None) or []
        if rows and isinstance(rows[0], dict):
            return rows[0], None
        return None, None
    except Exception as exc:
        return None, f"{table}: {type(exc).__name__}: {_clip(exc)}"


def _safe_insert(table: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        res = _sb().table(table).insert(payload).execute()
        return {"ok": True, "data": getattr(res, "data", None)}
    except Exception as exc:
        return {"ok": False, "error": f"{table}: {type(exc).__name__}: {_clip(exc)}"}


def _safe_update(table: str, payload: Dict[str, Any], **eq_filters: Any) -> Dict[str, Any]:
    try:
        q = _sb().table(table).update(payload)
        for col, val in eq_filters.items():
            q = q.eq(col, val)
        res = q.execute()
        return {"ok": True, "data": getattr(res, "data", None)}
    except Exception as exc:
        return {"ok": False, "error": f"{table}: {type(exc).__name__}: {_clip(exc)}"}


def _safe_upsert(table: str, payload: Dict[str, Any], on_conflict: str) -> Dict[str, Any]:
    try:
        res = _sb().table(table).upsert(payload, on_conflict=on_conflict).execute()
        return {"ok": True, "data": getattr(res, "data", None)}
    except Exception as exc:
        return {"ok": False, "error": f"{table}: {type(exc).__name__}: {_clip(exc)}"}


def _money(amount: int) -> str:
    return f"₦{amount:,.0f}"


# =============================================================================
# WhatsApp payload + outbound
# =============================================================================

def _extract_message(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        entry = (payload.get("entry") or [])[0]
        change = (entry.get("changes") or [])[0]
        value = change.get("value") or {}
        messages = value.get("messages") or []
        contacts = value.get("contacts") or []

        if not messages:
            return None

        message = messages[0] or {}
        contact = contacts[0] if contacts else {}

        wa_id = _normalize_phone(message.get("from") or contact.get("wa_id"))
        profile_name = _clean(((contact.get("profile") or {}).get("name")))
        msg_type = _lower(message.get("type"))
        text = ""

        if msg_type == "text":
            text = _clean(((message.get("text") or {}).get("body")))
        elif msg_type == "button":
            button = message.get("button") or {}
            text = _clean(button.get("text") or button.get("payload"))
        elif msg_type == "interactive":
            interactive = message.get("interactive") or {}
            interactive_type = _lower(interactive.get("type"))
            if interactive_type == "button_reply":
                reply = interactive.get("button_reply") or {}
                text = _clean(reply.get("title") or reply.get("id"))
            elif interactive_type == "list_reply":
                reply = interactive.get("list_reply") or {}
                text = _clean(reply.get("title") or reply.get("id"))

        return {
            "wa_id": wa_id,
            "from": wa_id,
            "display_phone": _display_phone(wa_id),
            "profile_name": profile_name,
            "message_id": _clean(message.get("id")),
            "type": msg_type,
            "text": text,
            "raw_message": message,
        }
    except Exception:
        return None


def _send_whatsapp_text(to: str, body: str) -> Dict[str, Any]:
    to = _normalize_phone(to)
    body = _clean(body)

    access_token = _clean(os.getenv("WHATSAPP_ACCESS_TOKEN") or os.getenv("META_WHATSAPP_TOKEN"))
    phone_number_id = _clean(os.getenv("WHATSAPP_PHONE_NUMBER_ID") or os.getenv("META_WHATSAPP_PHONE_NUMBER_ID"))

    if not to or not body:
        return {"ok": False, "error": "missing_to_or_body"}

    if not access_token or not phone_number_id:
        return {
            "ok": False,
            "error": "whatsapp_send_not_configured",
            "missing": {
                "WHATSAPP_ACCESS_TOKEN": not bool(access_token),
                "WHATSAPP_PHONE_NUMBER_ID": not bool(phone_number_id),
            },
            "fallback_body": body,
        }

    url = f"https://graph.facebook.com/v20.0/{phone_number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"preview_url": False, "body": body[:3900]},
    }

    try:
        response = requests.post(
            url,
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json=payload,
            timeout=20,
        )
        result: Dict[str, Any] = {"ok": response.status_code < 400, "status_code": response.status_code}
        try:
            result["response"] = response.json()
        except Exception:
            result["response_text"] = _clip(response.text)
        return result
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {_clip(exc)}"}


# =============================================================================
# State/context
# =============================================================================

def _get_session_state(wa_id: str) -> Dict[str, Any]:
    wa_id = _normalize_phone(wa_id)
    if not wa_id:
        return {"context": "main"}

    row, _err = _query_one("whatsapp_flow_sessions", "*", wa_id=wa_id)
    if row:
        return {
            "context": _clean(row.get("context") or "main"),
            "pending_action": _clean(row.get("pending_action")),
            "data": row.get("data") if isinstance(row.get("data"), dict) else {},
        }
    return {"context": "main", "pending_action": "", "data": {}}


def _set_session_state(wa_id: str, context: str = "main", pending_action: str = "", data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    wa_id = _normalize_phone(wa_id)
    if not wa_id:
        return {"ok": False, "error": "missing_wa_id"}

    payload = {
        "wa_id": wa_id,
        "context": context or "main",
        "pending_action": pending_action or None,
        "data": data or {},
        "updated_at": _now_iso(),
    }
    return _safe_upsert("whatsapp_flow_sessions", payload, on_conflict="wa_id")


# =============================================================================
# Account resolution
# =============================================================================

def _account_select_cols() -> str:
    return "id,account_id,provider,provider_user_id,auth_user_id,display_name,phone,phone_e164,email,updated_at,created_at"


def _account_id_from_row(row: Optional[Dict[str, Any]]) -> str:
    if not row:
        return ""
    return _clean(row.get("account_id") or row.get("id"))


def _find_account_by_wa(wa_id: str) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    wa_id = _normalize_phone(wa_id)
    debug: Dict[str, Any] = {"wa_id": wa_id, "steps": []}

    if not wa_id:
        return None, {**debug, "error": "missing_wa_id"}

    for provider in ("wa", "whatsapp"):
        row, err = _query_one("accounts", _account_select_cols(), provider=provider, provider_user_id=wa_id)
        debug["steps"].append({"table": "accounts", "provider": provider, "error": err, "found": bool(row)})
        if row:
            return row, debug

    for column in ("phone_e164", "phone"):
        for value in (_display_phone(wa_id), wa_id):
            row, err = _query_one("accounts", _account_select_cols(), **{column: value})
            debug["steps"].append({"table": "accounts", "column": column, "error": err, "found": bool(row)})
            if row:
                return row, debug

    for channel_type in ("whatsapp", "wa"):
        identity, err = _query_one("channel_identities", "*", channel_type=channel_type, provider_user_id=wa_id)
        debug["steps"].append({"table": "channel_identities", "channel_type": channel_type, "error": err, "found": bool(identity)})
        if identity:
            account_id = _clean(identity.get("account_id") or identity.get("owner_account_id"))
            if account_id:
                row, row_err = _query_one("accounts", _account_select_cols(), account_id=account_id)
                debug["steps"].append({"table": "accounts", "account_id": account_id, "error": row_err, "found": bool(row)})
                if row:
                    return row, debug
                return {"account_id": account_id, "provider": "wa", "provider_user_id": wa_id}, debug

    return None, debug


def _create_or_update_wa_account(wa_id: str, profile_name: str = "") -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    wa_id = _normalize_phone(wa_id)
    now_iso = _now_iso()

    existing, debug = _find_account_by_wa(wa_id)
    if existing:
        account_id = _account_id_from_row(existing)
        update_payload = {
            "provider": existing.get("provider") or "wa",
            "provider_user_id": existing.get("provider_user_id") or wa_id,
            "phone": existing.get("phone") or _display_phone(wa_id),
            "phone_e164": existing.get("phone_e164") or _display_phone(wa_id),
            "updated_at": now_iso,
        }
        if profile_name and not existing.get("display_name"):
            update_payload["display_name"] = profile_name
        if account_id:
            _safe_update("accounts", update_payload, account_id=account_id)
        return existing, {**debug, "created": False}

    payload = {
        "provider": "wa",
        "provider_user_id": wa_id,
        "display_name": profile_name or _display_phone(wa_id) or "WhatsApp User",
        "phone": _display_phone(wa_id),
        "phone_e164": _display_phone(wa_id),
        "created_at": now_iso,
        "updated_at": now_iso,
    }

    upsert = _safe_upsert("accounts", payload, on_conflict="provider,provider_user_id")
    debug["account_upsert"] = upsert

    row, find_debug = _find_account_by_wa(wa_id)
    debug["after_upsert_find"] = find_debug
    return row, {**debug, "created": True}


def _get_subscription(account_id: str) -> Optional[Dict[str, Any]]:
    row, _err = _query_one("user_subscriptions", "*", account_id=account_id)
    return row


def _plan_family(plan_code: Any) -> str:
    code = _lower(plan_code)
    if "business" in code:
        return "business"
    if "professional" in code:
        return "professional"
    if "starter" in code:
        return "starter"
    return "free"


def _is_active_paid_subscription(account_id: str) -> bool:
    sub = _get_subscription(account_id)
    if not sub:
        return False
    status = _lower(sub.get("status"))
    plan_code = _lower(sub.get("plan_code"))
    if status in {"inactive", "expired", "cancelled", "canceled", "disabled"}:
        return False
    return _plan_family(plan_code) in {"starter", "professional", "business"} and plan_code not in {"free", "free_forever", ""}


def _credit_balance(account_id: str) -> int:
    for table in ("ai_credit_balances", "credit_balances"):
        row, _err = _query_one(table, "*", account_id=account_id)
        if row:
            try:
                return int(row.get("balance") or row.get("credits") or row.get("credit_balance") or 0)
            except Exception:
                return 0
    return 0


def _plan_label(account_id: str) -> str:
    sub = _get_subscription(account_id)
    if not sub:
        return "Free Forever"

    name = _clean(sub.get("plan_name") or sub.get("plan_code") or "Free Forever")
    status = _clean(sub.get("status") or "active")
    expires = _clean(sub.get("expires_at") or sub.get("current_period_end") or "")
    if expires:
        return f"{name} ({status})\nExpires: {expires[:10]}"
    return f"{name} ({status})"


def _current_plan_code(account_id: str) -> str:
    sub = _get_subscription(account_id)
    if not sub:
        return "free"
    return _lower(sub.get("plan_code") or sub.get("plan") or "free")


def _subscription_status(account_id: str) -> str:
    sub = _get_subscription(account_id)
    if not sub:
        return "inactive"
    return _lower(sub.get("status") or "inactive")


def _subscription_expiry(account_id: str) -> str:
    sub = _get_subscription(account_id)
    if not sub:
        return ""
    return _clean(sub.get("expires_at") or sub.get("current_period_end") or sub.get("valid_until") or "")


def _same_active_plan(account_id: str, selected_plan_code: str) -> bool:
    return _subscription_status(account_id) in {"active", "trialing"} and _current_plan_code(account_id) == _lower(selected_plan_code)


def _plan_rank(plan_code: str) -> int:
    family = _plan_family(plan_code)
    if family == "business":
        return 3
    if family == "professional":
        return 2
    if family == "starter":
        return 1
    return 0


# =============================================================================
# Command recognition
# =============================================================================

def _match_alias(text: str, options: Dict[str, Dict[str, Any]]) -> List[str]:
    norm = _normalize_text(text)
    matches: List[str] = []

    for code, option in options.items():
        aliases = [code] + list(option.get("aliases") or [])
        for alias in aliases:
            alias_norm = _normalize_text(alias)
            if not alias_norm:
                continue
            if norm == alias_norm:
                matches.append(code)
                break
            if len(alias_norm) >= 5 and alias_norm in norm:
                matches.append(code)
                break

    return list(dict.fromkeys(matches))


def _match_amount(text: str, options: Dict[str, Dict[str, Any]]) -> List[str]:
    amounts = set(_extract_amounts(text))
    if not amounts:
        return []
    matches = []
    for code, option in options.items():
        price = int(option.get("price") or 0)
        credits = int(option.get("credits") or 0)
        if price in amounts:
            matches.append(code)
        elif _contains_credit_phrase(text) and credits in amounts:
            matches.append(code)
    return list(dict.fromkeys(matches))


def _recognize(text: str, context: str = "main") -> Dict[str, Any]:
    raw = _clean(text)
    norm = _normalize_text(raw)

    if not raw:
        return {"kind": "empty", "action": "menu"}

    # Exact/prefix command recognition must run before natural question fallback.
    # This guarantees "C1 986000" and "C2 profit ..." are calculators, not AI questions.
    prefix_match = re.match(r"^(s[1-3]|p[1-3]|b[1-3]|t(?:10|50|100|500)|f[1-8]|c[1-8])\\b", norm)
    if prefix_match:
        code = prefix_match.group(1).upper()
        if code in PLAN_OPTIONS:
            return {"kind": "plan", "code": code}
        if code in TOPUP_OPTIONS:
            return {"kind": "topup", "code": code}
        if code in TOOL_OPTIONS:
            return {"kind": "tool", "code": code, "action": TOOL_OPTIONS[code]["action"]}
        if code in CALC_OPTIONS:
            return {"kind": "calc", "code": code, "action": CALC_OPTIONS[code]["action"]}


    if norm in {"0", "menu", "main", "main menu", "start", "hello", "hi"}:
        return {"kind": "global", "action": "main_menu"}
    if norm in {"back", "go back", "*"}:
        return {"kind": "global", "action": "back"}
    if norm in {"cancel", "stop", "end"}:
        return {"kind": "global", "action": "cancel"}
    if norm in {"help", "8"}:
        return {"kind": "main", "action": "help"}

    main_map = {
        "1": "ask_prompt",
        "ask": "ask_prompt",
        "question": "ask_prompt",
        "tax question": "ask_prompt",
        "2": "credits",
        "credit": "credits",
        "credits": "credits",
        "balance": "credits",
        "usage credits": "credits",
        "3": "plan",
        "plan": "plan",
        "subscription": "plan",
        "current plan": "plan",
        "4": "plans_menu",
        "plans": "plans_menu",
        "upgrade": "plans_menu",
        "subscribe": "plans_menu",
        "5": "link_instruction",
        "link": "link_instruction",
        "connect": "link_instruction",
        "connect website": "link_instruction",
        "link account": "link_instruction",
        "6": "topup_menu",
        "topup": "topup_menu",
        "top up": "topup_menu",
        "buy credits": "topup_menu",
        "addon": "topup_menu",
        "add on": "topup_menu",
        "add ons": "topup_menu",
        "7": "tools_menu",
        "tools": "tools_menu",
        "tax tools": "tools_menu",
        "filing": "tools_menu",
        "tax filing": "tools_menu",
    }
    if norm in main_map:
        return {"kind": "main", "action": main_map[norm]}

    if norm.isdigit() and norm not in {"0", "1", "2", "3", "4", "5", "6", "7", "8"}:
        return {"kind": "invalid_menu", "action": "invalid_menu", "value": raw}

    plan_matches = list(dict.fromkeys(_match_alias(raw, PLAN_OPTIONS) + _match_amount(raw, PLAN_OPTIONS)))
    topup_matches = list(dict.fromkeys(_match_alias(raw, TOPUP_OPTIONS) + _match_amount(raw, TOPUP_OPTIONS)))
    tool_matches = _match_alias(raw, TOOL_OPTIONS)
    calc_matches = _match_alias(raw, CALC_OPTIONS)

    if context == "plans" and plan_matches:
        if len(plan_matches) == 1:
            return {"kind": "plan", "code": plan_matches[0]}
        return {"kind": "ambiguous", "area": "plans", "codes": plan_matches}

    if context == "topup" and topup_matches:
        if len(topup_matches) == 1:
            return {"kind": "topup", "code": topup_matches[0]}
        return {"kind": "ambiguous", "area": "topup", "codes": topup_matches}

    if context == "tools" and tool_matches:
        if len(tool_matches) == 1:
            return {"kind": "tool", "code": tool_matches[0], "action": TOOL_OPTIONS[tool_matches[0]]["action"]}
        return {"kind": "ambiguous", "area": "tools", "codes": tool_matches}

    if context == "calc" and calc_matches:
        if len(calc_matches) == 1:
            return {"kind": "calc", "code": calc_matches[0], "action": CALC_OPTIONS[calc_matches[0]]["action"]}
        return {"kind": "ambiguous", "area": "calc", "codes": calc_matches}

    # Context-free matching.
    combined = []
    for c in plan_matches:
        combined.append(("plan", c))
    for c in topup_matches:
        combined.append(("topup", c))
    for c in tool_matches:
        combined.append(("tool", c))
    for c in calc_matches:
        combined.append(("calc", c))

    if len(combined) == 1:
        kind, code = combined[0]
        if kind == "tool":
            return {"kind": kind, "code": code, "action": TOOL_OPTIONS[code]["action"]}
        if kind == "calc":
            return {"kind": kind, "code": code, "action": CALC_OPTIONS[code]["action"]}
        return {"kind": kind, "code": code}

    if len(combined) > 1:
        return {"kind": "ambiguous", "area": "mixed", "matches": combined}

    return {"kind": "question", "action": "ask_question"}


# =============================================================================
# Menus + descriptions
# =============================================================================

def _main_menu() -> str:
    return (
        "🇳🇬 Naija Tax Guide\n\n"
        "Reply with:\n"
        "1 - Ask a tax question\n"
        "2 - Check Usage Credits\n"
        "3 - Check current plan\n"
        "4 - View subscription plans\n"
        "5 - Link website account\n"
        "6 - Buy Usage Credit add-ons\n"
        "7 - Tax tools & filing\n"
        "8 - Help / Menu\n\n"
        "Global commands:\n"
        "0 or MENU - Main menu\n"
        "* or BACK - Go back\n"
        "CANCEL - Cancel current flow\n\n"
        "You can also type your Nigerian tax question directly."
    )


def _plans_menu() -> str:
    return (
        "📌 Subscription Plans\n\n"
        "S1 - Starter Monthly - ₦5,000 - 100 credits\n"
        "S2 - Starter Quarterly - ₦14,000 - 300 credits\n"
        "S3 - Starter Yearly - ₦51,000 - 1,200 credits\n\n"
        "P1 - Professional Monthly - ₦12,000 - 300 credits\n"
        "P2 - Professional Quarterly - ₦33,600 - 900 credits\n"
        "P3 - Professional Yearly - ₦122,400 - 3,600 credits\n\n"
        "B1 - Business Monthly - ₦25,000 - 800 credits\n"
        "B2 - Business Quarterly - ₦70,000 - 2,400 credits\n"
        "B3 - Business Yearly - ₦255,000 - 9,600 credits\n\n"
        "Reply with a code like S1, P1, or B1.\n"
        "You can also type Starter Monthly, ₦5,000, or 100 credits."
    )


def _topup_menu() -> str:
    return (
        "💎 Usage Credit Add-ons\n\n"
        "T10 - 10 credits - ₦500\n"
        "T50 - 50 credits - ₦2,000\n"
        "T100 - 100 credits - ₦3,500\n"
        "T500 - 500 credits - ₦15,000\n\n"
        "Reply with T10, T50, T100, or T500.\n"
        "Add-ons are available only to active paid subscribers."
    )


def _tools_menu() -> str:
    return (
        "📋 Tax Tools & Filing\n\n"
        "F1 - Tax calculators\n"
        "F2 - PAYE filing guide\n"
        "F3 - VAT filing guide\n"
        "F4 - CIT filing guide\n"
        "F5 - WHT guide\n"
        "F6 - Tax deadlines/calendar\n"
        "F7 - Filing checklist\n"
        "F8 - Back to main menu\n\n"
        "Reply with a code like F1."
    )


def _calc_menu() -> str:
    return (
        "🧮 Tax Calculators\n\n"
        "C1 - PAYE calculator\n"
        "C2 - Company Income Tax calculator\n"
        "C3 - VAT calculator\n"
        "C4 - Withholding Tax calculator\n"
        "C5 - Salary/net pay comparison\n"
        "C6 - Tax quiz\n"
        "C7 - Tax calendar/deadlines\n"
        "C8 - Back to Tax Tools\n\n"
        "Examples:\n"
        "C1 250000 monthly\n"
        "C2 profit 5000000 revenue 30000000\n"
        "C3 1000000\n"
        "C4 500000 5%"
    )


def _help_text() -> str:
    return (
        "Help - Naija Tax Guide\n\n"
        "• Main menu uses numbers 1–8.\n"
        "• Submenus use short codes like S1, T50, F1, and C1.\n"
        "• You can type natural words too, e.g. Starter Monthly or VAT calculator.\n"
        "• Database/cache answers may be served without credit charge.\n"
        "• AI answers require an active paid plan and Usage Credits.\n"
        "• Your web, WhatsApp, and Telegram channels share one credit wallet.\n\n"
        "Reply 0 for main menu."
    )


def _ambiguous_message(recognition: Dict[str, Any]) -> str:
    lines = ["I found more than one possible match. Please reply with the exact code:\n"]

    if recognition.get("area") == "mixed":
        for kind, code in recognition.get("matches") or []:
            if kind == "plan":
                item = PLAN_OPTIONS[code]
                lines.append(f"{code} - {item['name']} - {_money(item['price'])} - {item['credits']} credits")
            elif kind == "topup":
                item = TOPUP_OPTIONS[code]
                lines.append(f"{code} - {item['name']} - {_money(item['price'])} - {item['credits']} credits")
            elif kind == "tool":
                item = TOOL_OPTIONS[code]
                lines.append(f"{code} - {item['name']}")
            elif kind == "calc":
                item = CALC_OPTIONS[code]
                lines.append(f"{code} - {item['name']}")
        return "\n".join(lines)

    for code in recognition.get("codes") or []:
        if code in PLAN_OPTIONS:
            item = PLAN_OPTIONS[code]
            lines.append(f"{code} - {item['name']} - {_money(item['price'])} - {item['credits']} credits")
        elif code in TOPUP_OPTIONS:
            item = TOPUP_OPTIONS[code]
            lines.append(f"{code} - {item['name']} - {_money(item['price'])} - {item['credits']} credits")
        elif code in TOOL_OPTIONS:
            lines.append(f"{code} - {TOOL_OPTIONS[code]['name']}")
        elif code in CALC_OPTIONS:
            lines.append(f"{code} - {CALC_OPTIONS[code]['name']}")

    return "\n".join(lines)


# =============================================================================
# Link code
# =============================================================================

def _try_link_code(wa_id: str, text: str, profile_name: str = "") -> Optional[str]:
    code = _clean(text).upper().replace(" ", "")
    if not re.fullmatch(r"[A-Z0-9]{5,12}", code):
        return None

    token_tables = ("channel_link_tokens", "link_tokens", "channel_link_codes")
    token_row = None
    token_table = ""

    for table in token_tables:
        for provider_value in ("wa", "whatsapp"):
            try:
                q = _sb().table(table).select("*").eq("code", code)
                try:
                    q = q.eq("provider", provider_value)
                except Exception:
                    pass
                res = q.limit(1).execute()
                rows = getattr(res, "data", None) or []
                if rows and isinstance(rows[0], dict):
                    token_row = rows[0]
                    token_table = table
                    break
            except Exception:
                continue
        if token_row:
            break

    if not token_row:
        return None

    account_id = _clean(token_row.get("account_id") or token_row.get("owner_account_id") or token_row.get("app_user_id") or token_row.get("user_account_id"))
    if not account_id:
        return "The link code was found, but it is missing account ownership. Please generate a new code from the website."

    now_iso = _now_iso()
    wa_id = _normalize_phone(wa_id)
    account_row, _debug = _create_or_update_wa_account(wa_id, profile_name=profile_name)
    wa_account_id = _account_id_from_row(account_row)

    identity_payload = {
        "account_id": account_id,
        "channel_type": "whatsapp",
        "provider": "wa",
        "provider_user_id": wa_id,
        "display_name": profile_name or _display_phone(wa_id),
        "phone": _display_phone(wa_id),
        "status": "connected",
        "is_connected": True,
        "verified": True,
        "linked_at": now_iso,
        "updated_at": now_iso,
    }

    identity_result = _safe_upsert("channel_identities", identity_payload, on_conflict="account_id,channel_type,provider_user_id")

    if token_table and token_row.get("id"):
        _safe_update(
            token_table,
            {"status": "used", "used_at": now_iso, "provider_user_id": wa_id, "channel_account_id": wa_account_id or None},
            id=token_row.get("id"),
        )

    if not identity_result.get("ok"):
        return "I found your link code, but linking failed. Please contact support with this message: channel_identity_write_failed"

    return "✅ WhatsApp linked successfully.\n\nYou can now ask Nigeria tax questions here. Reply 0 for main menu."


# =============================================================================
# Payment initialization
# =============================================================================

def _checkout_email(account: Optional[Dict[str, Any]], account_id: str) -> str:
    email = _account_email(account)
    if email and not _is_placeholder_email(email):
        return email
    return f"user_{account_id[:8]}@naijataxguides.com"


def _init_paystack_checkout(account: Optional[Dict[str, Any]], account_id: str, item: Dict[str, Any], payment_type: str, wa_id: str = "") -> Dict[str, Any]:
    if create_reference is None or initialize_transaction is None:
        return {
            "ok": False,
            "message": f"{item['name']} selected.\n\nPlease complete payment from the web app:\n{_base_url()}/plans",
            "error": "paystack_service_unavailable",
        }

    prefix = "NTG-WA-TOPUP" if payment_type == "topup" else "NTG-WA"
    reference = create_reference(prefix)
    amount_kobo = int(item["price"]) * 100

    metadata = {
        "account_id": account_id,
        "source": "whatsapp",
        "channel_type": "whatsapp",
        "wa_id": _normalize_phone(wa_id),
        "customer_wa_id": _normalize_phone(wa_id),
        "bot_wa_phone": _whatsapp_bot_phone(),
        "return_channel": "whatsapp",
        "type": "credit_topup" if payment_type == "topup" else "subscription",
    }

    if payment_type == "topup":
        metadata.update(
            {
                "purpose": "usage_topup",
                "topup_code": item["code"],
                "package_code": item["code"],
                "package_name": item["name"],
                "credits": item["credits"],
                "amount_ngn": item["price"],
            }
        )
        callback_url = _whatsapp_return_url(
            wa_id,
            f"Payment completed for {item['name']}. Reference: {reference}. Please send this message to confirm my updated credits.",
        )
    else:
        metadata.update(
            {
                "plan_code": item["plan_code"],
                "plan_name": item["name"],
                "amount_ngn": item["price"],
                "credits": item["credits"],
            }
        )
        callback_url = _whatsapp_return_url(
            wa_id,
            f"Payment completed for {item['name']}. Reference: {reference}. Please send this message to confirm my plan.",
        )

    try:
        result = initialize_transaction(
            email=_checkout_email(account, account_id),
            amount_kobo=amount_kobo,
            reference=reference,
            callback_url=callback_url,
            metadata=metadata,
        )
        auth_url = ((result or {}).get("data") or {}).get("authorization_url") or (result or {}).get("authorization_url")
        if not auth_url:
            return {"ok": False, "error": "paystack_authorization_url_missing", "paystack_response": result}

        return {"ok": True, "authorization_url": auth_url, "reference": reference}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {_clip(exc)}"}


# =============================================================================
# Guides + calculators
# =============================================================================

def _guide(action: str) -> str:
    guides = {
        "paye_guide": (
            "PAYE Filing Guide\n\n"
            "1. Calculate monthly employee taxable income.\n"
            "2. Deduct PAYE based on applicable personal income tax bands.\n"
            "3. Remit PAYE to the relevant State Internal Revenue Service.\n"
            "4. Keep payroll schedules, payment receipts, and employee records.\n\n"
            "For full filing support, use the web app."
        ),
        "vat_guide": (
            "VAT Filing Guide\n\n"
            "1. Confirm if your business is VAT-registered.\n"
            "2. Record output VAT on taxable sales and input VAT on eligible purchases.\n"
            "3. File VAT returns and remit net VAT by the required deadline.\n"
            "4. Keep invoices and payment records.\n\n"
            "Standard VAT rate is commonly 7.5%, but confirm current rules for your transaction."
        ),
        "cit_guide": (
            "Company Income Tax Filing Guide\n\n"
            "1. Prepare financial statements.\n"
            "2. Compute taxable profit after allowable deductions.\n"
            "3. Apply the correct CIT rate based on company size/turnover.\n"
            "4. File returns with FIRS and keep all supporting records.\n\n"
            "Use a qualified accountant for final filing."
        ),
        "wht_guide": (
            "Withholding Tax Guide\n\n"
            "1. Confirm if the transaction is subject to WHT.\n"
            "2. Apply the correct WHT rate for the transaction type.\n"
            "3. Deduct WHT at payment point.\n"
            "4. Remit to the relevant tax authority and issue credit notes where applicable."
        ),
        "deadlines": (
            "Tax Deadlines / Calendar\n\n"
            "• PAYE: usually monthly remittance to the State IRS.\n"
            "• VAT: usually monthly filing/remittance.\n"
            "• WHT: remit according to applicable authority timelines.\n"
            "• CIT: annual company filing after financial year-end.\n\n"
            "Custom deadline creation is available on paid plans from the web app."
        ),
        "filing_checklist": (
            "Filing Checklist\n\n"
            "• Taxpayer/company registration details\n"
            "• TIN / CAC details where applicable\n"
            "• Sales and expense records\n"
            "• Payroll/PAYE records\n"
            "• VAT invoices\n"
            "• WHT receipts/credit notes\n"
            "• Bank statements and payment confirmations\n"
            "• Prior filings and assessment notices"
        ),
    }
    return guides.get(action) or _tools_menu()


def _calculate_paye(text: str) -> str:
    amounts = _extract_amounts(text)
    if not amounts:
        return (
            "PAYE Calculator\n\n"
            "Send your salary like this:\n"
            "C1 250000 monthly\n"
            "or\n"
            "C1 3000000 yearly\n\n"
            "This basic calculator is free."
        )

    amount = amounts[0]
    norm = _normalize_text(text)
    annual = amount * 12 if "month" in norm or "monthly" in norm else amount

    relief = max(200000, int(annual * 0.01)) + int(annual * 0.20)
    taxable = max(0, annual - relief)

    bands = [
        (300000, 0.07),
        (300000, 0.11),
        (500000, 0.15),
        (500000, 0.19),
        (1600000, 0.21),
        (10**15, 0.24),
    ]
    remaining = taxable
    tax = 0.0
    for band, rate in bands:
        if remaining <= 0:
            break
        take = min(remaining, band)
        tax += take * rate
        remaining -= take

    monthly_tax = tax / 12
    net_monthly = (annual / 12) - monthly_tax

    return (
        "PAYE Calculator Result\n\n"
        f"Gross annual income: {_money(annual)}\n"
        f"Estimated annual relief: {_money(relief)}\n"
        f"Estimated taxable income: {_money(int(taxable))}\n"
        f"Estimated annual PAYE: {_money(int(round(tax)))}\n"
        f"Estimated monthly PAYE: {_money(int(round(monthly_tax)))}\n"
        f"Estimated monthly net pay: {_money(int(round(net_monthly)))}\n\n"
        "Note: This is an estimate. Confirm pension, NHF, allowances, benefits, and state-specific treatment before final filing."
    )


def _calculate_cit(text: str) -> str:
    amounts = _extract_amounts(text)
    if not amounts:
        return (
            "CIT Calculator\n\n"
            "Send taxable profit and turnover like this:\n"
            "C2 profit 5000000 revenue 30000000\n\n"
            "This basic calculator is free."
        )

    norm = _normalize_text(text)
    profit = amounts[0]
    revenue = amounts[1] if len(amounts) > 1 else 0

    if revenue and revenue <= 25000000:
        rate = 0.0
        category = "Small company"
    elif revenue and revenue <= 100000000:
        rate = 0.20
        category = "Medium company"
    else:
        rate = 0.30
        category = "Large/unspecified company"

    tax = profit * rate

    return (
        "Company Income Tax Calculator Result\n\n"
        f"Taxable profit used: {_money(profit)}\n"
        f"Turnover/revenue used: {_money(revenue) if revenue else 'Not provided'}\n"
        f"Category: {category}\n"
        f"Estimated CIT rate: {int(rate * 100)}%\n"
        f"Estimated CIT: {_money(int(round(tax)))}\n\n"
        "Note: This is a basic estimate. Final CIT depends on allowable deductions, exemptions, and current FIRS rules."
    )


def _calculate_vat(text: str) -> str:
    amounts = _extract_amounts(text)
    if not amounts:
        return "VAT Calculator\n\nSend taxable sales amount like this:\nC3 1000000\n\nThis basic calculator is free."

    amount = amounts[0]
    vat = amount * 0.075
    total = amount + vat

    return (
        "VAT Calculator Result\n\n"
        f"Taxable amount: {_money(amount)}\n"
        "VAT rate used: 7.5%\n"
        f"VAT amount: {_money(int(round(vat)))}\n"
        f"Total including VAT: {_money(int(round(total)))}\n\n"
        "Note: Confirm that the transaction is VATable before charging VAT."
    )


def _calculate_wht(text: str) -> str:
    amounts = _extract_amounts(text)
    rate_match = re.search(r"(\d+(?:\.\d+)?)\s*%", _clean(text))
    rate = float(rate_match.group(1)) if rate_match else None

    if not amounts or rate is None:
        return (
            "WHT Calculator\n\n"
            "Send amount and WHT rate like this:\n"
            "C4 500000 5%\n\n"
            "WHT rates vary by transaction type, so include the rate."
        )

    amount = amounts[0]
    wht = amount * (rate / 100)

    return (
        "Withholding Tax Calculator Result\n\n"
        f"Transaction amount: {_money(amount)}\n"
        f"WHT rate used: {rate:g}%\n"
        f"WHT to deduct: {_money(int(round(wht)))}\n"
        f"Net payment after WHT: {_money(int(round(amount - wht)))}\n\n"
        "Note: Confirm the correct WHT rate for the transaction type."
    )


def _salary_compare(text: str) -> str:
    amounts = _extract_amounts(text)
    if len(amounts) < 2:
        return (
            "Salary Comparison\n\n"
            "Send two salary amounts like this:\n"
            "C5 250000 350000 monthly\n\n"
            "The app will estimate the net difference."
        )

    first = _calculate_paye(f"C1 {amounts[0]} monthly")
    second = _calculate_paye(f"C1 {amounts[1]} monthly")
    return (
        "Salary Comparison\n\n"
        "First salary estimate:\n"
        f"{first}\n\n"
        "Second salary estimate:\n"
        f"{second}"
    )


def _quiz_text() -> str:
    return (
        "Tax Quiz\n\n"
        "Question: Which tax is usually deducted from an employee's salary by the employer in Nigeria?\n\n"
        "A - VAT\n"
        "B - PAYE\n"
        "C - Company Income Tax\n"
        "D - Withholding Tax\n\n"
        "Reply with A, B, C, or D.\n"
        "Free users can access limited non-AI quiz attempts daily."
    )


def _handle_calculator_action(action: str, text: str) -> str:
    if action == "paye_calc":
        return _calculate_paye(text)
    if action == "cit_calc":
        return _calculate_cit(text)
    if action == "vat_calc":
        return _calculate_vat(text)
    if action == "wht_calc":
        return _calculate_wht(text)
    if action == "salary_compare":
        return _salary_compare(text)
    if action == "tax_quiz":
        return _quiz_text()
    if action == "deadlines":
        return _guide("deadlines")
    if action == "tools_menu":
        return _tools_menu()
    return _calc_menu()


# =============================================================================
# History
# =============================================================================

def _history_key(value: Any) -> str:
    text = _normalize_text(value)
    return re.sub(r"\s+", "_", text)[:180]


def _log_whatsapp_history(*, account_id: str, question: str, answer: str, result: Dict[str, Any]) -> Dict[str, Any]:
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    try:
        credits_consumed = int(meta.get("credits_consumed") or 0)
    except Exception:
        credits_consumed = 0

    usage_charged = bool(meta.get("usage_charged") is True or credits_consumed > 0)
    from_cache = bool(result.get("ok") is True and (result.get("source") == "database" or result.get("mode") == "direct_cache"))
    now_iso = _now_iso()

    payloads = [
        {
            "account_id": account_id or None,
            "question": _clip(question, 5000),
            "answer": _clip(answer, 20000),
            "lang": "en",
            "source": "whatsapp",
            "from_cache": from_cache,
            "canonical_key": _history_key(question),
            "normalized_question": _normalize_text(question),
            "plan_code": _clean(meta.get("plan_code")) or None,
            "credits_consumed": credits_consumed,
            "usage_charged": usage_charged,
            "channel": "whatsapp",
            "created_at": now_iso,
            "updated_at": now_iso,
        },
        {
            "account_id": account_id or None,
            "question": _clip(question, 5000),
            "answer": _clip(answer, 20000),
            "lang": "en",
            "source": "whatsapp",
            "from_cache": from_cache,
            "credits_consumed": credits_consumed,
            "usage_charged": usage_charged,
            "channel": "whatsapp",
            "created_at": now_iso,
        },
        {"question": _clip(question, 5000), "answer": _clip(answer, 20000), "created_at": now_iso},
    ]

    errors: List[str] = []
    for idx, payload in enumerate(payloads):
        inserted = _safe_insert("qa_history", payload)
        if inserted.get("ok"):
            return {"ok": True, "mode": f"whatsapp_direct_history_{idx}"}
        errors.append(str(inserted.get("error")))

    return {"ok": False, "error": "whatsapp_history_insert_failed", "errors": errors[:3]}


# =============================================================================
# Core action handlers
# =============================================================================

def _handle_plan_selection(wa_id: str, account: Dict[str, Any], account_id: str, code: str) -> Dict[str, Any]:
    item = PLAN_OPTIONS[code]

    if _same_active_plan(account_id, item["plan_code"]):
        expiry = _subscription_expiry(account_id)
        body = (
            f"You already have {item['name']} active.\n\n"
            f"Current balance: {_credit_balance(account_id)} Usage Credits\n"
            f"Plan expiry: {expiry[:10] if expiry else 'Not shown'}\n\n"
            "To upgrade, choose a higher plan like P1, P2, P3, B1, B2, or B3.\n"
            "Reply 4 to view plans or 0 for main menu."
        )
        return {"ok": True, "handled": "same_active_plan_blocked", "send_result": _send_whatsapp_text(wa_id, body)}

    current = _current_plan_code(account_id)
    if _plan_rank(current) > _plan_rank(item["plan_code"]) and _subscription_status(account_id) == "active":
        body = (
            f"You currently have a higher plan active:\n{_plan_label(account_id)}\n\n"
            f"You selected {item['name']}. Downgrades should be managed from web billing so your current access is not lost.\n\n"
            f"{_base_url()}/billing"
        )
        return {"ok": True, "handled": "downgrade_redirected", "send_result": _send_whatsapp_text(wa_id, body)}

    email_prompt = _ensure_email_or_prompt(
        wa_id=wa_id,
        account=account,
        pending_action="plan_selection",
        data={"code": code},
    )
    if email_prompt:
        return email_prompt

    checkout = _init_paystack_checkout(account, account_id, item, "subscription", wa_id=wa_id)

    if checkout.get("ok"):
        _set_session_state(
            wa_id,
            "payment_pending",
            "plan_selection",
            {
                "code": code,
                "reference": checkout.get("reference"),
                "authorization_url": checkout.get("authorization_url"),
                "created_at": _now_iso(),
            },
        )
        body = (
            f"{item['name']} selected.\n\n"
            f"Price: {_money(item['price'])}\n"
            f"Included Usage Credits: {item['credits']}\n\n"
            f"Complete payment here:\n{checkout['authorization_url']}\n\n"
            "After payment, Paystack should return you to this bot chat. A success message will also be sent after webhook confirmation."
        )
    else:
        body = (
            f"{item['name']} selected.\n\n"
            f"Price: {_money(item['price'])}\n"
            f"Included Usage Credits: {item['credits']}\n\n"
            f"Open the web app to complete payment:\n{_base_url()}/plans"
        )

    return {"ok": True, "handled": "plan_selection", "send_result": _send_whatsapp_text(wa_id, body), "checkout": checkout}


def _handle_topup_selection


def _handle_topup_selection(wa_id: str, account: Dict[str, Any], account_id: str, code: str) -> Dict[str, Any]:
    if not _is_active_paid_subscription(account_id):
        body = (
            "Usage Credit add-ons are available only to active paid subscribers.\n\n"
            "Please upgrade first:\n"
            f"{_base_url()}/plans\n\n"
            "Reply 4 to view plans or 0 for main menu."
        )
        return {"ok": True, "handled": "topup_blocked_no_paid_plan", "send_result": _send_whatsapp_text(wa_id, body)}

    item = TOPUP_OPTIONS[code]

    email_prompt = _ensure_email_or_prompt(
        wa_id=wa_id,
        account=account,
        pending_action="topup_selection",
        data={"code": code},
    )
    if email_prompt:
        return email_prompt

    checkout = _init_paystack_checkout(account, account_id, item, "topup", wa_id=wa_id)

    if checkout.get("ok"):
        _set_session_state(
            wa_id,
            "payment_pending",
            "topup_selection",
            {
                "code": code,
                "reference": checkout.get("reference"),
                "authorization_url": checkout.get("authorization_url"),
                "created_at": _now_iso(),
            },
        )
        body = (
            f"{item['name']} selected.\n\n"
            f"Credits: {item['credits']}\n"
            f"Price: {_money(item['price'])}\n\n"
            f"Complete payment here:\n{checkout['authorization_url']}\n\n"
            "Top-ups add credits only. They do not renew or extend your plan."
        )
    else:
        body = (
            f"{item['name']} selected.\n\n"
            f"Credits: {item['credits']}\n"
            f"Price: {_money(item['price'])}\n\n"
            f"Open the web app to buy add-ons:\n{_base_url()}/credits"
        )

    return {"ok": True, "handled": "topup_selection", "send_result": _send_whatsapp_text(wa_id, body), "checkout": checkout}


def _handle_text_message


def _handle_text_message(msg: Dict[str, Any]) -> Dict[str, Any]:
    wa_id = _normalize_phone(msg.get("wa_id"))
    text = _clean(msg.get("text"))
    profile_name = _clean(msg.get("profile_name"))

    if not wa_id:
        return {"ok": False, "error": "missing_wa_id"}

    state = _get_session_state(wa_id)
    context = _clean(state.get("context") or "main")

    if not text:
        _set_session_state(wa_id, "main")
        return {"ok": True, "handled": "empty_menu", "send_result": _send_whatsapp_text(wa_id, _main_menu())}

    link_reply = _try_link_code(wa_id, text, profile_name=profile_name)
    if link_reply:
        _set_session_state(wa_id, "main")
        return {"ok": True, "handled": "link_code", "send_result": _send_whatsapp_text(wa_id, link_reply)}

    account, account_debug = _create_or_update_wa_account(wa_id, profile_name=profile_name)
    account_id = _account_id_from_row(account)

    if not account_id:
        return {
            "ok": False,
            "error": "account_resolution_failed",
            "send_result": _send_whatsapp_text(wa_id, "I could not identify your account yet. Please try again or contact support."),
            "debug": account_debug if _debug_enabled() else None,
        }

    if context == "collect_email":
        if not _is_valid_email(text):
            return {
                "ok": True,
                "handled": "invalid_email",
                "send_result": _send_whatsapp_text(
                    wa_id,
                    "That does not look like a valid email address.\n\nPlease send your email like this: name@email.com\n\nReply CANCEL to stop.",
                ),
            }

        _safe_update("accounts", {"email": _lower(text), "updated_at": _now_iso()}, account_id=account_id)
        refreshed_account = dict(account or {})
        refreshed_account["email"] = _lower(text)

        pending_action = _clean(state.get("pending_action"))
        pending_data = state.get("data") if isinstance(state.get("data"), dict) else {}
        _set_session_state(wa_id, "main")

        if pending_action == "plan_selection" and pending_data.get("code") in PLAN_OPTIONS:
            return _handle_plan_selection(wa_id, refreshed_account, account_id, pending_data["code"])

        if pending_action == "topup_selection" and pending_data.get("code") in TOPUP_OPTIONS:
            return _handle_topup_selection(wa_id, refreshed_account, account_id, pending_data["code"])

        return {
            "ok": True,
            "handled": "email_saved",
            "send_result": _send_whatsapp_text(wa_id, "✅ Email saved successfully.\n\nReply 4 for plans or 6 for Usage Credit add-ons."),
        }

    payment_reference = _extract_payment_reference(text)
    if payment_reference:
        _set_session_state(wa_id, "main")
        body = (
            "✅ Payment reference received.\n\n"
            f"Reference: {payment_reference}\n\n"
            f"Current plan:\n{_plan_label(account_id)}\n\n"
            f"Available Usage Credits: {_credit_balance(account_id)}\n\n"
            "If you just paid and this has not updated yet, wait a few seconds and send 3 for plan or 2 for credits."
        )
        return {"ok": True, "handled": "payment_reference_status", "send_result": _send_whatsapp_text(wa_id, body)}

    recognition = _recognize(text, context)

    if recognition["kind"] == "global":
        action = recognition["action"]
        if action == "main_menu":
            _set_session_state(wa_id, "main")
            return {"ok": True, "handled": "main_menu", "send_result": _send_whatsapp_text(wa_id, _main_menu())}
        if action == "back":
            _set_session_state(wa_id, "main")
            return {"ok": True, "handled": "back_main", "send_result": _send_whatsapp_text(wa_id, _main_menu())}
        if action == "cancel":
            _set_session_state(wa_id, "main")
            return {"ok": True, "handled": "cancel", "send_result": _send_whatsapp_text(wa_id, "Current flow cancelled.\n\n" + _main_menu())}

    if recognition["kind"] == "invalid_menu":
        return {
            "ok": True,
            "handled": "invalid_menu_option",
            "send_result": _send_whatsapp_text(wa_id, "That menu option is not available yet.\n\nReply 0 for main menu, or type your Nigerian tax question in words."),
        }

    if recognition["kind"] == "ambiguous":
        return {"ok": True, "handled": "ambiguous", "send_result": _send_whatsapp_text(wa_id, _ambiguous_message(recognition))}

    if recognition["kind"] == "main":
        action = recognition["action"]
        if action == "ask_prompt":
            _set_session_state(wa_id, "ask")
            return {"ok": True, "handled": "ask_prompt", "send_result": _send_whatsapp_text(wa_id, "Please type your Nigerian tax question in one clear message.")}
        if action == "credits":
            return {"ok": True, "handled": "credits", "send_result": _send_whatsapp_text(wa_id, f"💎 Usage Credits\n\nAvailable balance: {_credit_balance(account_id)}\n\nReply 0 for main menu.")}
        if action == "plan":
            return {"ok": True, "handled": "plan", "send_result": _send_whatsapp_text(wa_id, f"📌 Current Plan\n\n{_plan_label(account_id)}\n\nUsage Credits: {_credit_balance(account_id)}\n\nReply 0 for main menu.")}
        if action == "plans_menu":
            _set_session_state(wa_id, "plans")
            return {"ok": True, "handled": "plans_menu", "send_result": _send_whatsapp_text(wa_id, _plans_menu())}
        if action == "link_instruction":
            _set_session_state(wa_id, "link")
            return {"ok": True, "handled": "link_instruction", "send_result": _send_whatsapp_text(wa_id, "To link your website account, open Channels on the web app, generate a WhatsApp link code, then send the code here.\n\nReply 0 for main menu.")}
        if action == "topup_menu":
            _set_session_state(wa_id, "topup")
            return {"ok": True, "handled": "topup_menu", "send_result": _send_whatsapp_text(wa_id, _topup_menu())}
        if action == "tools_menu":
            _set_session_state(wa_id, "tools")
            return {"ok": True, "handled": "tools_menu", "send_result": _send_whatsapp_text(wa_id, _tools_menu())}
        if action == "help":
            _set_session_state(wa_id, "main")
            return {"ok": True, "handled": "help", "send_result": _send_whatsapp_text(wa_id, _help_text())}

    if recognition["kind"] == "plan":
        _set_session_state(wa_id, "main")
        return _handle_plan_selection(wa_id, account or {}, account_id, recognition["code"])

    if recognition["kind"] == "topup":
        _set_session_state(wa_id, "main")
        return _handle_topup_selection(wa_id, account or {}, account_id, recognition["code"])

    if recognition["kind"] == "tool":
        action = recognition.get("action")
        if action == "calculator_menu":
            _set_session_state(wa_id, "calc")
            return {"ok": True, "handled": "calculator_menu", "send_result": _send_whatsapp_text(wa_id, _calc_menu())}
        if action == "main_menu":
            _set_session_state(wa_id, "main")
            return {"ok": True, "handled": "main_menu", "send_result": _send_whatsapp_text(wa_id, _main_menu())}
        return {"ok": True, "handled": action, "send_result": _send_whatsapp_text(wa_id, _guide(str(action)))}

    if recognition["kind"] == "calc":
        action = recognition.get("action")
        if action == "tools_menu":
            _set_session_state(wa_id, "tools")
            return {"ok": True, "handled": "tools_menu", "send_result": _send_whatsapp_text(wa_id, _tools_menu())}
        return {"ok": True, "handled": action, "send_result": _send_whatsapp_text(wa_id, _handle_calculator_action(str(action), text))}

    # Default: natural tax question.
    result = ask_guarded(
        {
            "account_id": account_id,
            "question": text,
            "lang": "en",
            "channel": "whatsapp",
            "provider": "wa",
            "provider_user_id": wa_id,
            "action_code": "ai_tax_answer",
        }
    )

    answer = _clean(result.get("answer") or result.get("message") or "I could not generate an answer right now. Please try again shortly.")
    _log_whatsapp_history(account_id=account_id, question=text, answer=answer, result=result if isinstance(result, dict) else {})

    meta = result.get("meta") if isinstance(result, dict) and isinstance(result.get("meta"), dict) else {}
    result_ok = bool(isinstance(result, dict) and result.get("ok") is True)

    credit_note = ""
    if result_ok and meta.get("usage_charged") is True:
        credit_note = f"\n\n💎 Credit used: {meta.get('credits_consumed') or meta.get('credit_cost') or 1}. Balance: {meta.get('credits_left', 'not shown')}."
    elif result_ok and (result.get("source") == "database" or result.get("mode") == "direct_cache"):
        credit_note = "\n\n✅ Served from saved database/cache. No new credit charged."
    elif not result_ok and result.get("error") in {"paid_plan_required", "insufficient_credits"}:
        credit_note = "\n\nNo credit was charged for this blocked request."

    return {
        "ok": True,
        "handled": "tax_question",
        "account_id": account_id,
        "send_result": _send_whatsapp_text(wa_id, _clip(answer + credit_note + "\n\nReply 0 for main menu.", 3900)),
        "usage_charged": meta.get("usage_charged"),
        "credits_consumed": meta.get("credits_consumed"),
        "debug": {"recognition": recognition, "account_debug": account_debug} if _debug_enabled() else None,
    }


# =============================================================================
# Routes
# =============================================================================

@bp.route("/webhook", methods=["GET", "POST"], strict_slashes=False)
@bp.route("/whatsapp/webhook", methods=["GET", "POST"], strict_slashes=False)
def whatsapp_webhook():
    """
    Meta WhatsApp webhook.

    Both route patterns are intentionally registered because backend projects
    commonly register this blueprint either with:
      url_prefix="/api"
    or:
      url_prefix="/api/whatsapp"

    This prevents POST /api/whatsapp/webhook from returning 405 after deploy.
    """
    if request.method == "GET":
        verify_token = _clean(os.getenv("WHATSAPP_VERIFY_TOKEN") or os.getenv("META_VERIFY_TOKEN"))
        mode = _clean(request.args.get("hub.mode"))
        token = _clean(request.args.get("hub.verify_token"))
        challenge = _clean(request.args.get("hub.challenge"))

        if mode == "subscribe" and verify_token and token == verify_token:
            return challenge, 200

        if not mode and not token and not challenge:
            return jsonify(
                {
                    "ok": True,
                    "service": "whatsapp_webhook",
                    "version": WHATSAPP_FLOW_VERSION,
                    "methods": ["GET", "POST"],
                    "valid_paths_depend_on_blueprint_prefix": [
                        "/api/whatsapp/webhook",
                        "/api/webhook",
                        "/api/whatsapp/whatsapp/webhook",
                    ],
                    "configured": {
                        "verify_token": bool(verify_token),
                        "access_token": bool(_clean(os.getenv("WHATSAPP_ACCESS_TOKEN") or os.getenv("META_WHATSAPP_TOKEN"))),
                        "phone_number_id": bool(_clean(os.getenv("WHATSAPP_PHONE_NUMBER_ID") or os.getenv("META_WHATSAPP_PHONE_NUMBER_ID"))),
                        "bot_phone": bool(_whatsapp_bot_phone()),
                    },
                }
            ), 200

        return jsonify({"ok": False, "error": "verification_failed"}), 403

    payload = _safe_json()
    msg = _extract_message(payload)

    if not msg:
        # Meta can send message status updates and other non-message callbacks.
        return jsonify({"ok": True, "ignored": True, "version": WHATSAPP_FLOW_VERSION}), 200

    result = _handle_text_message(msg)

    # Always acknowledge Meta with 200 after handling attempt to avoid retries.
    body: Dict[str, Any] = {
        "ok": True,
        "handled": result.get("handled", "message"),
        "version": WHATSAPP_FLOW_VERSION,
    }
    if _debug_enabled():
        body["debug"] = result

    return jsonify(body), 200


@bp.route("/health", methods=["GET"], strict_slashes=False)
@bp.route("/whatsapp/health", methods=["GET"], strict_slashes=False)
def whatsapp_health():
    return jsonify(
        {
            "ok": True,
            "service": "whatsapp",
            "version": WHATSAPP_FLOW_VERSION,
            "configured": {
                "verify_token": bool(_clean(os.getenv("WHATSAPP_VERIFY_TOKEN") or os.getenv("META_VERIFY_TOKEN"))),
                "access_token": bool(_clean(os.getenv("WHATSAPP_ACCESS_TOKEN") or os.getenv("META_WHATSAPP_TOKEN"))),
                "phone_number_id": bool(_clean(os.getenv("WHATSAPP_PHONE_NUMBER_ID") or os.getenv("META_WHATSAPP_PHONE_NUMBER_ID"))),
            },
        }
    ), 200


@bp.route("/test-reply", methods=["POST"], strict_slashes=False)
@bp.route("/whatsapp/test-reply", methods=["POST"], strict_slashes=False)
def whatsapp_test_reply():
    data = _safe_json()
    result = _send_whatsapp_text(_normalize_phone(data.get("to")), _clean(data.get("text") or "Naija Tax Guide WhatsApp test message."))
    return jsonify(result), 200 if result.get("ok") else 400
