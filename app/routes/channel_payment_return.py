# app/routes/channel_promo.py
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlencode

from flask import Blueprint, jsonify, request

from app.core.supabase_client import supabase
from app.services.outbound_service import send_whatsapp_text, send_telegram_text
from app.services.paystack_service import create_reference, initialize_transaction
from app.services.promo_service import (
    bootstrap_account_promo_state,
    calculate_promo_checkout_preview,
    record_promo_checkout_started,
    validate_promo_code,
)

logger = logging.getLogger(__name__)

bp = Blueprint("channel_promo", __name__)

CHANNEL_PROMO_ROUTE_VERSION = "2026-05-31-batch36C1-direct-channel-promo-bridge"

PLAN_CODE_MAP: Dict[str, str] = {
    "S1": "starter_monthly",
    "S2": "starter_quarterly",
    "S3": "starter_yearly",
    "P1": "professional_monthly",
    "P2": "professional_quarterly",
    "P3": "professional_yearly",
    "B1": "business_monthly",
    "B2": "business_quarterly",
    "B3": "business_yearly",
    "1": "starter_monthly",
    "2": "starter_quarterly",
    "3": "starter_yearly",
    "4": "professional_monthly",
    "5": "professional_quarterly",
    "6": "professional_yearly",
    "7": "business_monthly",
    "8": "business_quarterly",
    "9": "business_yearly",
}

DEFAULT_PLAN_FALLBACKS: Dict[str, Dict[str, Any]] = {
    "starter_monthly": {"plan_code": "starter_monthly", "name": "Starter Monthly", "price": 5000, "credits": 100, "duration_days": 30, "family": "starter", "billing_cycle": "monthly"},
    "starter_quarterly": {"plan_code": "starter_quarterly", "name": "Starter Quarterly", "price": 14000, "credits": 300, "duration_days": 90, "family": "starter", "billing_cycle": "quarterly"},
    "starter_yearly": {"plan_code": "starter_yearly", "name": "Starter Yearly", "price": 51000, "credits": 1200, "duration_days": 365, "family": "starter", "billing_cycle": "yearly"},
    "professional_monthly": {"plan_code": "professional_monthly", "name": "Professional Monthly", "price": 12000, "credits": 300, "duration_days": 30, "family": "professional", "billing_cycle": "monthly"},
    "professional_quarterly": {"plan_code": "professional_quarterly", "name": "Professional Quarterly", "price": 33600, "credits": 900, "duration_days": 90, "family": "professional", "billing_cycle": "quarterly"},
    "professional_yearly": {"plan_code": "professional_yearly", "name": "Professional Yearly", "price": 122400, "credits": 3600, "duration_days": 365, "family": "professional", "billing_cycle": "yearly"},
    "business_monthly": {"plan_code": "business_monthly", "name": "Business Monthly", "price": 25000, "credits": 800, "duration_days": 30, "family": "business", "billing_cycle": "monthly"},
    "business_quarterly": {"plan_code": "business_quarterly", "name": "Business Quarterly", "price": 70000, "credits": 2400, "duration_days": 90, "family": "business", "billing_cycle": "quarterly"},
    "business_yearly": {"plan_code": "business_yearly", "name": "Business Yearly", "price": 255000, "credits": 9600, "duration_days": 365, "family": "business", "billing_cycle": "yearly"},
}


def _sb():
    return supabase() if callable(supabase) else supabase


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _lower(value: Any) -> str:
    return _clean(value).lower()


def _upper(value: Any) -> str:
    return _clean(value).upper()


def _clip(value: Any, limit: int = 1200) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit] + "...<truncated>"


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(Decimal(str(value).replace(",", "")))
    except Exception:
        return default


def _money_from_kobo(kobo: Any) -> str:
    amount = _to_int(kobo, 0) / 100
    return f"₦{amount:,.0f}"


def _rows(resp: Any) -> list[dict[str, Any]]:
    data = getattr(resp, "data", None)
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def _first(resp: Any) -> Optional[dict[str, Any]]:
    rows = _rows(resp)
    return rows[0] if rows else None


def _safe_exec(builder: Any) -> tuple[bool, Any, str]:
    try:
        resp = builder.execute()
        return True, resp, ""
    except Exception as exc:
        return False, None, f"{type(exc).__name__}: {_clip(exc)}"


def _normalize_promo_code(value: Any) -> str:
    code = _upper(value)
    return re.sub(r"[^A-Z0-9_-]+", "", code)[:80]


def _extract_promo_code(text: str) -> str:
    raw = _clean(text)
    if not raw:
        return ""

    patterns = [
        r"(?:^|\s)/(?:start)\s+promo[_\-\s:]+([A-Za-z0-9_-]{3,80})\b",
        r"(?:^|\s)start\s+promo[_\-\s:]+([A-Za-z0-9_-]{3,80})\b",
        r"(?:^|\s)promo\s+([A-Za-z0-9_-]{3,80})\b",
        r"(?:^|\s)promo[_\-]([A-Za-z0-9_-]{3,80})\b",
        r"(?:^|\s)start\s+([A-Za-z0-9_-]{3,80})\b",
    ]
    for pat in patterns:
        m = re.search(pat, raw, flags=re.I)
        if m:
            candidate = _normalize_promo_code(m.group(1))
            if candidate:
                check = validate_promo_code(candidate)
                if check.get("valid"):
                    return candidate

    # Bare promo code support. Only accept if it is a valid active promo code.
    bare = _normalize_promo_code(raw)
    if 3 <= len(bare) <= 80 and bare not in {"MENU", "START", "HELP", "BACK", "CANCEL"}:
        try:
            check = validate_promo_code(bare)
            if check.get("valid"):
                return bare
        except Exception:
            return ""
    return ""


def _extract_plan_code(text: str) -> str:
    token = _upper((_clean(text).split() or [""])[0])
    token = re.sub(r"[^A-Z0-9]+", "", token)
    return PLAN_CODE_MAP.get(token, "")


def _extract_email(text: str) -> str:
    m = re.search(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", _clean(text), flags=re.I)
    return m.group(0).lower() if m else ""


def _extract_whatsapp_message(payload: Dict[str, Any]) -> Optional[Dict[str, str]]:
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
        wa_id = re.sub(r"\D+", "", str(message.get("from") or contact.get("wa_id") or ""))
        text = ""
        msg_type = _lower(message.get("type"))
        if msg_type == "text":
            text = _clean(((message.get("text") or {}).get("body")))
        elif msg_type == "button":
            btn = message.get("button") or {}
            text = _clean(btn.get("text") or btn.get("payload"))
        elif msg_type == "interactive":
            interactive = message.get("interactive") or {}
            if _lower(interactive.get("type")) == "button_reply":
                reply = interactive.get("button_reply") or {}
                text = _clean(reply.get("title") or reply.get("id"))
            elif _lower(interactive.get("type")) == "list_reply":
                reply = interactive.get("list_reply") or {}
                text = _clean(reply.get("title") or reply.get("id"))
        if not wa_id or not text:
            return None
        return {"channel_type": "whatsapp", "provider_user_id": wa_id, "text": text, "display_name": _clean(((contact.get("profile") or {}).get("name")))}
    except Exception:
        return None


def _extract_telegram_message(payload: Dict[str, Any]) -> Optional[Dict[str, str]]:
    try:
        msg = payload.get("message") or payload.get("edited_message") or {}
        if not isinstance(msg, dict):
            return None
        chat = msg.get("chat") or {}
        user = msg.get("from") or {}
        chat_id = _clean(chat.get("id") or user.get("id"))
        user_id = _clean(user.get("id") or chat_id)
        text = _clean(msg.get("text") or "")
        if not chat_id or not user_id or not text:
            return None
        name = _clean(" ".join([_clean(user.get("first_name")), _clean(user.get("last_name"))]).strip() or user.get("username"))
        return {"channel_type": "telegram", "provider_user_id": user_id, "chat_id": chat_id, "text": text, "display_name": name}
    except Exception:
        return None


def _get_channel_message() -> Optional[Dict[str, str]]:
    path = request.path.rstrip("/").lower()
    if request.method.upper() != "POST":
        return None
    payload = request.get_json(silent=True) or {}
    if path in {"/api/whatsapp/webhook", "/api/webhook", "/api/whatsapp/whatsapp/webhook"}:
        return _extract_whatsapp_message(payload)
    if path in {"/api/telegram/webhook", "/api/telegram", "/api/telegram/update"}:
        return _extract_telegram_message(payload)
    return None


def _send_channel_text(channel_type: str, provider_user_id: str, text: str) -> Dict[str, Any]:
    if request.args.get("dry_run") in {"1", "true", "yes"}:
        return {"ok": True, "dry_run": True, "text": text[:1200]}
    try:
        if channel_type == "whatsapp":
            return {"ok": bool(send_whatsapp_text(provider_user_id, text))}
        if channel_type == "telegram":
            return {"ok": bool(send_telegram_text(provider_user_id, text))}
        return {"ok": False, "error": "unsupported_channel"}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {_clip(exc)}"}


def _account_row_by_channel(channel_type: str, provider_user_id: str) -> Optional[Dict[str, Any]]:
    aliases = [channel_type]
    if channel_type == "whatsapp":
        aliases = ["whatsapp", "wa"]
    elif channel_type == "telegram":
        aliases = ["telegram", "tg"]

    for ch in aliases:
        ok, resp, _ = _safe_exec(_sb().table("channel_identities").select("*").eq("channel_type", ch).eq("provider_user_id", provider_user_id).limit(1))
        if ok:
            row = _first(resp)
            if row and row.get("account_id"):
                account_id = _clean(row.get("account_id"))
                ok2, resp2, _ = _safe_exec(_sb().table("accounts").select("*").eq("account_id", account_id).limit(1))
                acct = _first(resp2) if ok2 else None
                if acct:
                    return acct
                return {"account_id": account_id, "id": account_id, "provider": ch, "provider_user_id": provider_user_id}

    providers = ["wa", "whatsapp"] if channel_type == "whatsapp" else ["tg", "telegram"]
    for provider in providers:
        ok, resp, _ = _safe_exec(_sb().table("accounts").select("*").eq("provider", provider).eq("provider_user_id", provider_user_id).limit(1))
        if ok:
            row = _first(resp)
            if row:
                owner = _clean(row.get("auth_user_id") or row.get("owner_account_id") or row.get("linked_account_id"))
                if owner:
                    ok2, resp2, _ = _safe_exec(_sb().table("accounts").select("*").eq("account_id", owner).limit(1))
                    acct = _first(resp2) if ok2 else None
                    return acct or {"account_id": owner, "id": owner, "provider": "web", "provider_user_id": owner}
                return row
    return None


def _account_id_from_row(row: Dict[str, Any]) -> str:
    return _clean(row.get("account_id") or row.get("id") or row.get("auth_user_id"))


def _account_from_known_account_id(account_id: str) -> Dict[str, Any]:
    """
    Batch 36C1 bridge helper.

    WhatsApp and Telegram routes already resolve the correct effective account_id
    before command handling.  When they call the promo handler directly, prefer
    that account_id so the promo redemption attaches to the web owner account,
    not a temporary channel shell account.
    """
    account_id = _clean(account_id)
    if not account_id:
        return {"ok": False, "account_id": "", "row": {}, "created": False}

    try:
        ok, resp, _ = _safe_exec(_sb().table("accounts").select("*").eq("account_id", account_id).limit(1))
        row = _first(resp) if ok else None
    except Exception:
        row = None

    return {"ok": True, "account_id": account_id, "row": row or {"account_id": account_id, "id": account_id}, "created": False}


def _ensure_channel_account(channel_type: str, provider_user_id: str, display_name: str = "") -> Dict[str, Any]:
    row = _account_row_by_channel(channel_type, provider_user_id)
    if row:
        return {"ok": True, "account_id": _account_id_from_row(row), "row": row, "created": False}

    provider = "wa" if channel_type == "whatsapp" else "tg"
    account_id = str(provider_user_id)
    payload = {
        "provider": provider,
        "provider_user_id": provider_user_id,
        "display_name": display_name or f"{channel_type.title()} User",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    if channel_type == "whatsapp":
        payload["phone"] = provider_user_id
        payload["phone_e164"] = provider_user_id

    try:
        _sb().table("accounts").upsert(payload, on_conflict="provider,provider_user_id").execute()
    except Exception:
        try:
            _sb().table("accounts").insert(payload).execute()
        except Exception:
            logger.exception("Channel promo account upsert failed")

    row = _account_row_by_channel(channel_type, provider_user_id) or payload
    return {"ok": True, "account_id": _account_id_from_row(row) or account_id, "row": row, "created": True}


def _get_or_create_session(channel_type: str, provider_user_id: str, account_id: str = "") -> Dict[str, Any]:
    try:
        ok, resp, _ = _safe_exec(_sb().table("promo_channel_sessions").select("*").eq("channel_type", channel_type).eq("provider_user_id", provider_user_id).limit(1))
        row = _first(resp) if ok else None
        if row:
            return row
        payload = {"channel_type": channel_type, "provider_user_id": provider_user_id, "account_id": account_id or None, "created_at": _now_iso(), "updated_at": _now_iso(), "metadata": {}}
        ok2, resp2, _ = _safe_exec(_sb().table("promo_channel_sessions").insert(payload))
        return _first(resp2) if ok2 else payload
    except Exception:
        return {"channel_type": channel_type, "provider_user_id": provider_user_id, "account_id": account_id}


def _update_session(channel_type: str, provider_user_id: str, patch: Dict[str, Any]) -> None:
    try:
        payload = {**patch, "updated_at": _now_iso()}
        _sb().table("promo_channel_sessions").upsert({"channel_type": channel_type, "provider_user_id": provider_user_id, **payload}, on_conflict="channel_type,provider_user_id").execute()
    except Exception:
        logger.exception("Promo channel session update failed")


def _get_active_promo_redemption(account_id: str) -> Optional[Dict[str, Any]]:
    if not account_id:
        return None
    try:
        ok, resp, _ = _safe_exec(
            _sb().table("promo_redemptions")
            .select("*")
            .eq("account_id", account_id)
            .in_("status", ["pending", "applied"])
            .order("created_at", desc=True)
            .limit(1)
        )
        return _first(resp) if ok else None
    except Exception:
        return None


def _get_plan(plan_code: str) -> Dict[str, Any]:
    fallback = DEFAULT_PLAN_FALLBACKS.get(plan_code) or {"plan_code": plan_code, "name": plan_code.replace("_", " ").title(), "price": 0, "credits": 0, "family": plan_code.split("_", 1)[0], "billing_cycle": "monthly", "duration_days": 30}
    try:
        ok, resp, _ = _safe_exec(_sb().table("plans").select("*").eq("plan_code", plan_code).limit(1))
        row = _first(resp) if ok else None
        if not row:
            return fallback
        price = _to_int(row.get("price"), _to_int(fallback.get("price"), 0))
        duration = _to_int(row.get("duration_days"), _to_int(fallback.get("duration_days"), 30))
        cycle = "yearly" if duration >= 365 or "yearly" in plan_code else "quarterly" if duration >= 90 or "quarterly" in plan_code else "monthly"
        monthly_credits = _to_int(row.get("ai_credits_total"), _to_int(fallback.get("credits"), 0))
        multiplier = 12 if cycle == "yearly" else 3 if cycle == "quarterly" else 1
        return {
            "plan_code": plan_code,
            "name": _clean(row.get("name")) or fallback.get("name"),
            "price": price,
            "credits": monthly_credits * multiplier,
            "duration_days": duration,
            "family": plan_code.split("_", 1)[0],
            "billing_cycle": cycle,
        }
    except Exception:
        return fallback


def _public_backend_base_url() -> str:
    for key in ("PUBLIC_BACKEND_BASE_URL", "BACKEND_PUBLIC_URL", "APP_BASE_URL", "KOYEB_PUBLIC_DOMAIN"):
        value = _clean(os.getenv(key))
        if value:
            return value.rstrip("/") if value.startswith("http") else f"https://{value.rstrip('/')}"
    return "https://incredible-nonie-bmsconcept-37359733.koyeb.app"


def _transaction_insert(payload: Dict[str, Any]) -> Dict[str, Any]:
    attempts = [payload, {k: v for k, v in payload.items() if k != "amount_kobo"}, {k: v for k, v in payload.items() if k not in {"amount_kobo", "metadata"}}]
    last = ""
    for item in attempts:
        ok, resp, err = _safe_exec(_sb().table("paystack_transactions").insert(item))
        if ok:
            return {"ok": True, "row": _first(resp)}
        last = err
    return {"ok": False, "error": last}


def _create_discounted_channel_checkout(channel_type: str, provider_user_id: str, account_id: str, plan_code: str, email: str = "") -> Dict[str, Any]:
    plan = _get_plan(plan_code)
    original_amount_kobo = _to_int(plan.get("price"), 0) * 100
    if original_amount_kobo <= 0:
        return {"ok": False, "error": "invalid_plan_price", "message": "❌ This plan price is not available right now. Please try again later."}

    promo_preview = calculate_promo_checkout_preview(account_id=account_id, plan_code=plan_code, original_amount_kobo=original_amount_kobo)
    if not promo_preview.get("applies"):
        return {"ok": False, "error": "promo_not_applicable", "message": "No active promo discount was found for this account. Reply PROMO TAXWITHBM first, or use the normal plan menu."}

    final_amount_kobo = _to_int(promo_preview.get("final_amount_kobo"), original_amount_kobo)
    discount_amount_kobo = _to_int(promo_preview.get("discount_amount_kobo"), 0)
    redemption = promo_preview.get("redemption") if isinstance(promo_preview.get("redemption"), dict) else {}
    promo_code = _clean(redemption.get("promo_code") or promo_preview.get("promo_code"))
    reference = create_reference("NTG")

    metadata = {
        "account_id": account_id,
        "plan_code": plan_code,
        "plan_name": plan.get("name"),
        "plan_family": plan.get("family"),
        "type": "subscription",
        "source": "channel_promo_batch36C",
        "channel_type": channel_type,
        "provider_user_id": provider_user_id,
        "amount_ngn": int(final_amount_kobo / 100),
        "amount_kobo": final_amount_kobo,
        "original_amount_ngn": int(original_amount_kobo / 100),
        "original_amount_kobo": original_amount_kobo,
        "discount_amount_ngn": int(discount_amount_kobo / 100),
        "discount_amount_kobo": discount_amount_kobo,
        "final_amount_ngn": int(final_amount_kobo / 100),
        "final_amount_kobo": final_amount_kobo,
        "promo_applied": True,
        "promo_code": promo_code,
        "promo_redemption_id": redemption.get("id") if redemption else None,
        "promo_benefit_type": redemption.get("benefit_type") if redemption else None,
        "promo_discount_percent": redemption.get("discount_percent") if redemption else None,
        "channel_promo_route_version": CHANNEL_PROMO_ROUTE_VERSION,
        "currency": "NGN",
    }

    qs = urlencode({"reference": reference, "channel_type": channel_type, "provider_user_id": provider_user_id, "account_id": account_id, "plan_code": plan_code})
    callback_url = f"{_public_backend_base_url()}/api/channel/payment/return?{qs}"

    tx_payload = {
        "reference": reference,
        "account_id": account_id,
        "amount": final_amount_kobo,
        "amount_kobo": final_amount_kobo,
        "currency": "NGN",
        "status": "pending",
        "plan_code": plan_code,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "metadata": metadata,
    }
    tx_note = _transaction_insert(tx_payload)

    result = initialize_transaction(email=email or None, amount_kobo=final_amount_kobo, reference=reference, metadata=metadata, callback_url=callback_url)
    auth_url = ((result or {}).get("data") or {}).get("authorization_url") or (result or {}).get("authorization_url")
    if not auth_url:
        return {"ok": False, "error": "paystack_authorization_url_missing", "message": "❌ Could not create payment link. Please try again shortly.", "tx_note": tx_note}

    checkout_note = record_promo_checkout_started(
        account_id=account_id,
        payment_reference=reference,
        plan_code=plan_code,
        original_amount_kobo=original_amount_kobo,
        discount_amount_kobo=discount_amount_kobo,
        final_amount_kobo=final_amount_kobo,
        metadata=metadata,
    )

    cycle = plan.get("billing_cycle") or "monthly"
    body = (
        f"🎟️ *Promo Applied: {promo_code}*\n\n"
        f"📋 Plan: {plan.get('name')}\n"
        f"Original price: {_money_from_kobo(original_amount_kobo)}\n"
        f"Promo discount: -{_money_from_kobo(discount_amount_kobo)}\n"
        f"Amount to pay: {_money_from_kobo(final_amount_kobo)}\n"
        f"Credits: {plan.get('credits')} AI credits per {cycle}\n"
        f"Reference: {reference}\n\n"
        f"Click to pay:\n{auth_url}\n\n"
        "After successful payment, your plan will activate automatically."
    )
    return {"ok": True, "message": body, "payment_link": auth_url, "reference": reference, "pricing": promo_preview, "transaction_note": tx_note, "checkout_note": checkout_note}


def _promo_success_message(code: str, result: Dict[str, Any]) -> str:
    captured = bool(result.get("captured"))
    reason = _clean(result.get("reason"))
    if captured or reason == "promo_already_attached_to_account":
        return (
            f"✅ *Promo code applied: {code}*\n\n"
            "You are eligible for the promo discount on your first paid subscription.\n\n"
            "Choose a plan to continue:\n"
            "S1 - Starter Monthly\nS2 - Starter Quarterly\nS3 - Starter Yearly\n"
            "P1 - Professional Monthly\nB1 - Business Monthly\n\n"
            "Example: reply S1 to get your discounted payment link."
        )
    if reason == "referral_already_attached_to_account":
        return (
            "⚠️ A referral is already attached to this account.\n\n"
            "To avoid double rewards, this account cannot also use a promo code. You can still continue with the normal plan menu."
        )
    if result.get("ok") is False:
        return "⚠️ I could not apply this promo code right now. Please try again shortly."
    return f"⚠️ Promo code {code} could not be applied. Reason: {reason or 'not eligible'}."


def _handle_promo_capture(msg: Dict[str, str], code: str) -> Dict[str, Any]:
    channel_type = msg["channel_type"]
    provider_user_id = msg["provider_user_id"]
    provided_account_id = _clean(msg.get("account_id"))
    account = _account_from_known_account_id(provided_account_id) if provided_account_id else _ensure_channel_account(channel_type, provider_user_id, msg.get("display_name") or "")
    account_id = _clean(account.get("account_id"))

    if request.args.get("dry_run") in {"1", "true", "yes"}:
        return {"ok": True, "handled": "promo_capture_dry_run", "channel_type": channel_type, "provider_user_id": provider_user_id, "account_id": account_id, "promo_code": code}

    result = bootstrap_account_promo_state(account_id=account_id, promo_code=code, source=f"{channel_type}_promo_capture_batch36C")
    _update_session(channel_type, provider_user_id, {"account_id": account_id, "promo_code": code, "last_promo_capture_result": result})
    body = _promo_success_message(code, result)
    send_result = _send_channel_text(channel_type, msg.get("chat_id") or provider_user_id, body)
    return {"ok": True, "handled": "promo_capture", "channel_type": channel_type, "provider_user_id": provider_user_id, "account_id": account_id, "promo_code": code, "bootstrap": result, "send_result": send_result}


def _handle_promo_plan(msg: Dict[str, str], plan_code: str) -> Optional[Dict[str, Any]]:
    channel_type = msg["channel_type"]
    provider_user_id = msg["provider_user_id"]
    provided_account_id = _clean(msg.get("account_id"))
    account = _account_from_known_account_id(provided_account_id) if provided_account_id else _ensure_channel_account(channel_type, provider_user_id, msg.get("display_name") or "")
    account_id = _clean(account.get("account_id"))
    if not account_id:
        return None

    redemption = _get_active_promo_redemption(account_id)
    if not redemption:
        return None

    if request.args.get("dry_run") in {"1", "true", "yes"}:
        return {"ok": True, "handled": "promo_plan_dry_run", "channel_type": channel_type, "provider_user_id": provider_user_id, "account_id": account_id, "plan_code": plan_code, "promo_code": redemption.get("promo_code")}

    email = _clean((account.get("row") or {}).get("email") if isinstance(account.get("row"), dict) else "")
    try:
        result = _create_discounted_channel_checkout(channel_type, provider_user_id, account_id, plan_code, email=email)
        _update_session(channel_type, provider_user_id, {"account_id": account_id, "promo_code": redemption.get("promo_code"), "pending_plan_code": plan_code, "last_checkout_result": result})
        message = result.get("message") if result.get("ok") else result.get("message") or "❌ Could not create promo payment link. Please try again."
        send_result = _send_channel_text(channel_type, msg.get("chat_id") or provider_user_id, message)
        return {"ok": True, "handled": "promo_discounted_checkout", "channel_type": channel_type, "provider_user_id": provider_user_id, "account_id": account_id, "plan_code": plan_code, "checkout": result, "send_result": send_result}
    except Exception as exc:
        logger.exception("Channel promo checkout failed")
        body = "❌ I could not create your promo payment link right now. Please try again shortly or use the website."
        send_result = _send_channel_text(channel_type, msg.get("chat_id") or provider_user_id, body)
        return {"ok": True, "handled": "promo_checkout_failed", "error": f"{type(exc).__name__}: {_clip(exc)}", "send_result": send_result}


def process_channel_promo_text(
    *,
    channel_type: str,
    provider_user_id: str,
    text: str,
    account_id: str = "",
    display_name: str = "",
    chat_id: str = "",
) -> Optional[Dict[str, Any]]:
    """
    Direct bridge used by app.routes.whatsapp and app.routes.telegram.

    This is intentionally callable from inside the existing bot routes. It means
    promo commands are handled before the normal AI question fallback, so commands
    like START PROMO TAXWITHBM cannot consume Usage Credits.
    """
    channel_type = _lower(channel_type)
    provider_user_id = _clean(provider_user_id)
    text = _clean(text)

    if channel_type not in {"whatsapp", "telegram"} or not provider_user_id or not text:
        return None

    msg: Dict[str, str] = {
        "channel_type": channel_type,
        "provider_user_id": provider_user_id,
        "text": text,
        "display_name": _clean(display_name),
        "chat_id": _clean(chat_id) or provider_user_id,
        "account_id": _clean(account_id),
    }

    promo_code = _extract_promo_code(text)
    if promo_code:
        return _handle_promo_capture(msg, promo_code)

    plan_code = _extract_plan_code(text)
    if plan_code:
        return _handle_promo_plan(msg, plan_code)

    return None


@bp.before_app_request
def _channel_promo_interceptor():
    msg = _get_channel_message()
    if not msg:
        return None

    text = msg.get("text") or ""
    promo_code = _extract_promo_code(text)
    if promo_code:
        result = _handle_promo_capture(msg, promo_code)
        return jsonify({"ok": True, "intercepted": True, "route_version": CHANNEL_PROMO_ROUTE_VERSION, **result}), 200

    plan_code = _extract_plan_code(text)
    if plan_code:
        result = _handle_promo_plan(msg, plan_code)
        if result:
            return jsonify({"ok": True, "intercepted": True, "route_version": CHANNEL_PROMO_ROUTE_VERSION, **result}), 200

    return None


@bp.get("/channel-promo/health")
def channel_promo_health():
    return jsonify({
        "ok": True,
        "route_version": CHANNEL_PROMO_ROUTE_VERSION,
        "message": "WhatsApp and Telegram promo interceptor/direct bridge is active.",
        "captures": ["START PROMO TAXWITHBM", "PROMO TAXWITHBM", "/start promo_TAXWITHBM", "TAXWITHBM"],
        "discount_rule": "If account has pending/applied promo_redemption, channel plan checkout gets promo discount.",
        "webhook_paths_intercepted": ["/api/whatsapp/webhook", "/api/webhook", "/api/telegram/webhook"],
    }), 200


@bp.get("/channel-promo/test-extract")
def channel_promo_test_extract():
    text = request.args.get("text") or ""
    return jsonify({
        "ok": True,
        "route_version": CHANNEL_PROMO_ROUTE_VERSION,
        "text": text,
        "promo_code": _extract_promo_code(text),
        "plan_code": _extract_plan_code(text),
    }), 200
