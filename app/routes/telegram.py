from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

from flask import Blueprint, jsonify, request

from app.core.supabase_client import supabase
from app.services.accounts_service import upsert_account
from app.services.ask_service import ask_guarded
from app.services.channel_linking_service import consume_and_link, unlink_channel
from app.services.outbound_service import send_telegram_text

bp = Blueprint("telegram", __name__)

LINK_CODE_RE = re.compile(r"^[A-Z0-9]{8}$")
MENU_TRIGGERS = {"menu", "start", "help", "/start", "/menu", "/help", "hi", "hello"}
NUMERIC_OPTIONS = {"1", "2", "3", "4", "5", "6", "7"}

def _sb():
    return supabase() if callable(supabase) else supabase

def _clip(value: Any, n: int = 220) -> str:
    s = str(value or "")
    return s if len(s) <= n else s[:n] + "…"

def _is_link_code(text: str) -> bool:
    return bool(LINK_CODE_RE.match(str(text or "").strip().upper()))

def _extract_update(body: Dict[str, Any]) -> tuple[str, str]:
    msg = body.get("message") or body.get("edited_message") or {}
    chat = msg.get("chat") or {}
    from_user = msg.get("from") or {}
    chat_id = str(chat.get("id") or "").strip()
    provider_user_id = str(from_user.get("id") or chat_id or "").strip()
    text = str(msg.get("text") or "").strip()
    return provider_user_id, text

def _get_linked_identity(provider_user_id: str) -> Optional[Dict[str, Any]]:
    try:
        resp = (
            _sb()
            .table("channel_identities")
            .select("*")
            .eq("channel_type", "telegram")
            .eq("provider_user_id", provider_user_id)
            .limit(1)
            .execute()
        )
        rows = getattr(resp, "data", None) or []
        return rows[0] if rows else None
    except Exception:
        return None

def _resolve_linked_account_id(provider_user_id: str) -> str:
    identity = _get_linked_identity(provider_user_id)
    return str((identity or {}).get("account_id") or "").strip()

def _link_failure_text(reason: str) -> str:
    reason = str(reason or "").strip().lower()
    if reason == "invalid_code":
        return "❌ Link failed.\nReason: invalid_code\nDetails: n/a\nFix: Generate a fresh link code on the website and send it here."
    if reason == "used_code":
        return "❌ Link failed.\nReason: used_code\nDetails: n/a\nFix: Generate a fresh link code on the website and send it here."
    if reason == "expired_code":
        return "❌ Link failed.\nReason: expired_code\nDetails: n/a\nFix: Generate a fresh link code on the website and send it here."
    if reason == "channel_belongs_to_another_user":
        return "❌ Link failed.\nReason: channel_belongs_to_another_user\nDetails: n/a\nFix: Reply 5 to unlink this Telegram account first, then send a fresh code."
    if reason in {"channel_limit_reached", "telegram_channel_limit_reached", "whatsapp_channel_limit_reached"}:
        return "❌ Link failed.\nReason: channel_limit_reached\nFix: You have reached your current plan channel limit. Unlink an existing channel or upgrade your plan."
    if reason == "subscription_required_for_channel_linking":
        return "❌ Link failed.\nReason: subscription_required\nFix: Activate a paid plan on the website before linking channels."
    return (
        "❌ Link failed.\n"
        f"Reason: {_clip(reason)}\n"
        "Details: n/a\n"
        "Fix: Check link token flow and accounts link update."
    )

def _welcome_menu(linked: bool) -> str:
    action_line = "5 — Unlink website account" if linked else "5 — Link website account"
    return (
        "Welcome to Naija Tax Guide ✅\n\n"
        "Reply with:\n"
        "1 — Ask a tax question\n"
        "2 — Check AI credits balance\n"
        "3 — Check current plan\n"
        "4 — Upgrade subscription\n"
        f"{action_line}\n"
        "6 — Referral / invite a friend\n"
        "7 — Help / how to use this bot\n\n"
        "You can also type your tax question directly at any time."
    )

def _send_onboarding(provider_user_id: str) -> None:
    send_telegram_text(
        provider_user_id,
        "Website account linking is optional.\n\n"
        "If you already use the website and want this Telegram account connected to it:\n"
        "1) Login on the website\n"
        "2) Generate your LINK CODE\n"
        "3) Reply here with the 8-character code\n\n"
        "Example: 7K9M2H8P"
    )

def _credit_summary(account_id: str) -> str:
    try:
        b = _sb().table("ai_credit_balances").select("balance,updated_at").eq("account_id", account_id).limit(1).execute()
        d = _sb().table("ai_daily_usage").select("count,day,updated_at").eq("account_id", account_id).limit(1).execute()
        brow = (getattr(b, "data", None) or [{}])[0]
        drow = (getattr(d, "data", None) or [{}])[0]
        return (
            "AI Credits Summary:\n\n"
            f"Current balance: {brow.get('balance') if brow.get('balance') is not None else 'Not available'}\n"
            f"Used recently: {drow.get('count') if drow.get('count') is not None else 'Not available'}\n"
            f"Usage record date: {drow.get('day') or 'Not available'}\n"
            f"Last updated: {brow.get('updated_at') or drow.get('updated_at') or 'Not available'}"
        )
    except Exception as e:
        return f"❌ Could not check AI credits right now.\nReason: {_clip(e)}"

def _plan_summary(account_id: str) -> str:
    try:
        r = (
            _sb().table("user_subscriptions").select("*").eq("account_id", account_id).order("created_at", desc=True).limit(1).execute()
        )
        rows = getattr(r, "data", None) or []
        row = rows[0] if rows else {}
        if not row:
            return "Current Plan:\n\nNo active subscription found. Send 4 if you want to see upgrade options."
        return (
            "Current Plan:\n\n"
            f"Plan code: {row.get('plan_code') or 'Not available'}\n"
            f"Status: {row.get('status') or ('active' if row.get('is_active') else 'inactive')}\n"
            f"Started: {row.get('started_at') or row.get('starts_at') or row.get('created_at') or 'Not available'}\n"
            f"Expires: {row.get('expires_at') or row.get('ends_at') or 'Not available'}"
        )
    except Exception as e:
        return f"❌ Could not check your current plan right now.\nReason: {_clip(e)}"

def _referral_summary(account_id: str) -> str:
    try:
        prof = _sb().table("referral_profiles").select("*").eq("account_id", account_id).limit(1).execute()
        prow = (getattr(prof, "data", None) or [{}])[0]
        code = prow.get("referral_code") or prow.get("code") or "Not available"
        refs = _sb().table("referrals").select("id", count="exact").eq("referrer_account_id", account_id).execute()
        count = getattr(refs, "count", None)
        if count is None:
            count = len(getattr(refs, "data", None) or [])
        return "Referral / Invite a Friend:\n\n" + f"Referral code: {code}\n" + f"Total referrals: {int(count or 0)}"
    except Exception as e:
        return f"❌ Could not load your referral details right now.\nReason: {_clip(e)}"

def _handle_link_or_unlink(provider_user_id: str, linked_account_id: str):
    if linked_account_id:
        result = unlink_channel(provider="tg", provider_user_id=provider_user_id)
        if result.get("ok"):
            send_telegram_text(provider_user_id, "✅ This Telegram account has been unlinked. Generate a fresh code on the website and send it here as your first message to relink.")
            return jsonify({"ok": True, "linked": False, "mode": "unlink", "unlink": result})
        send_telegram_text(provider_user_id, "❌ Could not unlink this Telegram account right now. Please try again later.")
        return jsonify({"ok": True, "linked": True, "mode": "unlink_failed", "unlink": result})
    _send_onboarding(provider_user_id)
    return jsonify({"ok": True, "linked": False, "mode": "link_help"})

def _handle_menu_option(provider_user_id: str, linked_account_id: str, option: str):
    if option == "1":
        send_telegram_text(provider_user_id, "Please type your tax question and I will answer.")
        return jsonify({"ok": True, "linked": bool(linked_account_id), "mode": "ask_prompt"})
    if option == "2":
        if not linked_account_id:
            _send_onboarding(provider_user_id)
            return jsonify({"ok": True, "linked": False, "mode": "needs_link_for_credits"})
        send_telegram_text(provider_user_id, _credit_summary(linked_account_id))
        return jsonify({"ok": True, "linked": True, "mode": "credits"})
    if option == "3":
        if not linked_account_id:
            _send_onboarding(provider_user_id)
            return jsonify({"ok": True, "linked": False, "mode": "needs_link_for_plan"})
        send_telegram_text(provider_user_id, _plan_summary(linked_account_id))
        return jsonify({"ok": True, "linked": True, "mode": "plan"})
    if option == "4":
        if not linked_account_id:
            _send_onboarding(provider_user_id)
            return jsonify({"ok": True, "linked": False, "mode": "needs_link_for_upgrade"})
        send_telegram_text(provider_user_id, "Upgrade subscription on the website dashboard or send a plan name like starter quarterly.")
        return jsonify({"ok": True, "linked": True, "mode": "upgrade"})
    if option == "5":
        return _handle_link_or_unlink(provider_user_id, linked_account_id)
    if option == "6":
        if not linked_account_id:
            _send_onboarding(provider_user_id)
            return jsonify({"ok": True, "linked": False, "mode": "needs_link_for_referral"})
        send_telegram_text(provider_user_id, _referral_summary(linked_account_id))
        return jsonify({"ok": True, "linked": True, "mode": "referral"})
    if option == "7":
        send_telegram_text(provider_user_id, _welcome_menu(bool(linked_account_id)))
        return jsonify({"ok": True, "linked": bool(linked_account_id), "mode": "help"})
    return None

@bp.post("/telegram/webhook")
def telegram_webhook():
    body = request.get_json(silent=True) or {}
    try:
        provider_user_id, text = _extract_update(body)
        if not provider_user_id:
            return jsonify({"ok": True, "ignored": True})
        upsert_account(provider="tg", provider_user_id=provider_user_id, display_name=None, phone=None)
        linked_account_id = _resolve_linked_account_id(provider_user_id)
        normalized = (text or "").strip()
        lowered = normalized.lower()
        if lowered == "unlink":
            result = unlink_channel(provider="tg", provider_user_id=provider_user_id)
            if result.get("ok"):
                send_telegram_text(provider_user_id, "✅ This Telegram account has been unlinked. Generate a fresh code on the website and send it here as your first message to relink.")
                return jsonify({"ok": True, "linked": False, "mode": "unlink", "unlink": result})
            send_telegram_text(provider_user_id, "❌ Could not unlink this Telegram account right now. Please try again later.")
            return jsonify({"ok": True, "linked": bool(linked_account_id), "mode": "unlink_failed", "unlink": result})
        if lowered in MENU_TRIGGERS:
            send_telegram_text(provider_user_id, _welcome_menu(bool(linked_account_id)))
            return jsonify({"ok": True, "linked": bool(linked_account_id), "mode": "menu"})
        if lowered in NUMERIC_OPTIONS:
            handled = _handle_menu_option(provider_user_id, linked_account_id, lowered)
            if handled is not None:
                return handled
        if not linked_account_id:
            if normalized and _is_link_code(normalized):
                attempt = consume_and_link(provider="tg", code=normalized.upper(), provider_user_id=provider_user_id, display_name=None, phone=None)
                if attempt.get("ok"):
                    send_telegram_text(provider_user_id, "✅ Telegram linked successfully!\nNow send your tax question here anytime.\n\nReply 7 anytime to see the menu.")
                    return jsonify({"ok": True, "linked": True, "linked_now": True, "account_id": attempt.get("account_id")})
                send_telegram_text(provider_user_id, _link_failure_text(attempt.get("reason") or attempt.get("error")))
                return jsonify({"ok": True, "linked": False, "attempt": attempt})
            _send_onboarding(provider_user_id)
            return jsonify({"ok": True, "linked": False, "mode": "onboarding"})
        if not normalized:
            send_telegram_text(provider_user_id, _welcome_menu(True))
            return jsonify({"ok": True, "linked": True, "ignored": True, "reason": "no_text"})
        if _is_link_code(normalized):
            send_telegram_text(provider_user_id, "✅ This Telegram account is already linked.\nReply 5 if you want to unlink it first.")
            return jsonify({"ok": True, "linked": True, "ignored": True, "reason": "already_linked"})
        resp = ask_guarded(account_id=linked_account_id, question=normalized, lang="en", channel="telegram")
        answer = str(resp.get("answer") or resp.get("message") or "").strip() or "I couldn't process that right now. Please try again."
        send_telegram_text(provider_user_id, answer)
        return jsonify({"ok": True, "linked": True, "account_id": linked_account_id, "ask": resp})
    except Exception as e:
        logging.exception("Telegram webhook error: %s", e)
        return jsonify({"ok": True})
