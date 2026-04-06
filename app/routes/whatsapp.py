from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
from typing import Any, Dict, Optional, Tuple

from flask import Blueprint, jsonify, request

from app.core.supabase_client import supabase
from app.services.accounts_service import upsert_account
from app.services.ask_service import ask_guarded
from app.services.channel_identity_service import get_channel_identity
from app.services.channel_linking_service import consume_and_link, unlink_channel
from app.services.outbound_service import send_whatsapp_text

bp = Blueprint("whatsapp", __name__)

WA_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "").strip()
WA_APP_SECRET = os.getenv("WHATSAPP_APP_SECRET", "").strip()
LINK_CODE_RE = re.compile(r"^[A-Z0-9]{8}$")
MENU_TRIGGERS = {"menu", "start", "help", "hi", "hello"}


def _sb():
    return supabase() if callable(supabase) else supabase


def _clip(value: Any, n: int = 220) -> str:
    s = str(value or "")
    return s if len(s) <= n else s[:n] + "…"


def _verify_meta_signature(raw_body: bytes) -> bool:
    if not WA_APP_SECRET:
        return True
    signature = (request.headers.get("X-Hub-Signature-256") or "").strip()
    if not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(WA_APP_SECRET.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


def _extract_message(body: Dict[str, Any]) -> Tuple[str, str]:
    entry = (body.get("entry") or [None])[0] or {}
    changes = (entry.get("changes") or [None])[0] or {}
    value = changes.get("value") or {}
    messages = value.get("messages") or []
    if not messages:
        return "", ""
    msg = messages[0]
    from_phone = str(msg.get("from") or "").strip()
    msg_type = msg.get("type")
    text = ""
    if msg_type == "text":
        text = str((msg.get("text") or {}).get("body") or "").strip()
    return from_phone, text


def _is_link_code(text: str) -> bool:
    return bool(LINK_CODE_RE.match(str(text or "").strip().upper()))


def _get_linked_identity(from_phone: str) -> Optional[Dict[str, Any]]:
    try:
        return get_channel_identity(channel_type="whatsapp", provider_user_id=from_phone)
    except Exception:
        return None


def _resolve_linked_account_id(from_phone: str) -> str:
    identity = _get_linked_identity(from_phone)
    return str((identity or {}).get("account_id") or "").strip()


def _link_failure_text(reason: str) -> str:
    reason = str(reason or "").strip().lower()
    if reason == "invalid_code":
        return "❌ Link failed. The code is invalid. Please generate a fresh WhatsApp LINK CODE on the website and send it here again."
    if reason == "used_code":
        return "❌ Link failed. That code has already been used. Please generate a fresh WhatsApp LINK CODE on the website and send it here again."
    if reason == "expired_code":
        return "❌ Link failed. That code has expired. Please generate a fresh WhatsApp LINK CODE on the website and send it here again."
    if reason == "channel_belongs_to_another_user":
        return "❌ This WhatsApp number is already linked to another account. Reply 5 to unlink it first, then send a fresh code."
    return (
        "❌ Link failed. Please generate a fresh WhatsApp LINK CODE on the website and send it here again.\n"
        f"Reason: {_clip(reason)}"
    )


def _welcome_menu(linked: bool) -> str:
    if linked:
        action_line = "5 — Unlink website account"
    else:
        action_line = "5 — Link website account"

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


def _send_onboarding(from_phone: str) -> None:
    send_whatsapp_text(
        from_phone,
        "Welcome to Naija Tax Guide ✅\n\n"
        "To link this WhatsApp number to your website account:\n"
        "1) Login on the website\n"
        "2) Generate your WhatsApp LINK CODE\n"
        "3) Send the 8-character code here as your first message\n\n"
        "Example: 7K9M2H8P\n\n"
        "Reply 7 anytime to see the menu.",
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
            _sb()
            .table("user_subscriptions")
            .select("*")
            .eq("account_id", account_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
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
        return (
            "Referral / Invite a Friend:\n\n"
            f"Referral code: {code}\n"
            f"Total referrals: {int(count or 0)}"
        )
    except Exception as e:
        return f"❌ Could not load your referral details right now.\nReason: {_clip(e)}"


def _handle_link_or_unlink(from_phone: str, linked_account_id: str):
    if linked_account_id:
        result = unlink_channel(provider="wa", provider_user_id=from_phone)
        if result.get("ok"):
            send_whatsapp_text(
                from_phone,
                "✅ This WhatsApp number has been unlinked.\n"
                "Generate a fresh WhatsApp LINK CODE on the website and send it here as your first message to relink."
            )
            return jsonify({"ok": True, "linked": False, "mode": "unlink", "unlink": result})

        send_whatsapp_text(from_phone, "❌ Could not unlink this WhatsApp right now. Please try again later.")
        return jsonify({"ok": True, "linked": True, "mode": "unlink_failed", "unlink": result})

    send_whatsapp_text(
        from_phone,
        "To link this WhatsApp number to your website account:\n"
        "1) Login on the website\n"
        "2) Generate your WhatsApp LINK CODE\n"
        "3) Send the 8-character code here\n\n"
        "Example: 7K9M2H8P"
    )
    return jsonify({"ok": True, "linked": False, "mode": "link_help"})


def _handle_menu_option(from_phone: str, linked_account_id: str, option: str):
    if option == "1":
        send_whatsapp_text(from_phone, "Please type your tax question and I will answer.")
        return jsonify({"ok": True, "linked": bool(linked_account_id), "mode": "ask_prompt"})

    if option == "2":
        if not linked_account_id:
            _send_onboarding(from_phone)
            return jsonify({"ok": True, "linked": False, "mode": "needs_link_for_credits"})
        send_whatsapp_text(from_phone, _credit_summary(linked_account_id))
        return jsonify({"ok": True, "linked": True, "mode": "credits"})

    if option == "3":
        if not linked_account_id:
            _send_onboarding(from_phone)
            return jsonify({"ok": True, "linked": False, "mode": "needs_link_for_plan"})
        send_whatsapp_text(from_phone, _plan_summary(linked_account_id))
        return jsonify({"ok": True, "linked": True, "mode": "plan"})

    if option == "4":
        if not linked_account_id:
            _send_onboarding(from_phone)
            return jsonify({"ok": True, "linked": False, "mode": "needs_link_for_upgrade"})
        send_whatsapp_text(
            from_phone,
            "Upgrade subscription on the website dashboard or send a plan name like starter quarterly."
        )
        return jsonify({"ok": True, "linked": True, "mode": "upgrade"})

    if option == "5":
        return _handle_link_or_unlink(from_phone, linked_account_id)

    if option == "6":
        if not linked_account_id:
            _send_onboarding(from_phone)
            return jsonify({"ok": True, "linked": False, "mode": "needs_link_for_referral"})
        send_whatsapp_text(from_phone, _referral_summary(linked_account_id))
        return jsonify({"ok": True, "linked": True, "mode": "referral"})

    if option == "7":
        send_whatsapp_text(from_phone, _welcome_menu(bool(linked_account_id)))
        return jsonify({"ok": True, "linked": bool(linked_account_id), "mode": "help"})

    return None


@bp.get("/whatsapp/webhook")
def wa_webhook_verify():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token and WA_VERIFY_TOKEN and token == WA_VERIFY_TOKEN:
        return (challenge or ""), 200
    return "Forbidden", 403


@bp.post("/whatsapp/webhook")
def wa_webhook_receive():
    raw_body = request.get_data(cache=True, as_text=False) or b""
    if not _verify_meta_signature(raw_body):
        return jsonify({"ok": False, "error": "invalid_signature"}), 403

    body = request.get_json(silent=True) or {}

    try:
        from_phone, text = _extract_message(body)
        if not from_phone:
            return jsonify({"ok": True, "ignored": True})

        upsert_account(
            provider="wa",
            provider_user_id=from_phone,
            display_name=None,
            phone=from_phone,
        )

        linked_account_id = _resolve_linked_account_id(from_phone)
        normalized = (text or "").strip()
        lowered = normalized.lower()

        if lowered == "unlink":
            result = unlink_channel(provider="wa", provider_user_id=from_phone)
            if result.get("ok"):
                send_whatsapp_text(
                    from_phone,
                    "✅ This WhatsApp number has been unlinked.\n"
                    "Generate a fresh WhatsApp LINK CODE on the website and send it here as your first message to relink."
                )
                return jsonify({"ok": True, "linked": False, "mode": "unlink", "unlink": result})

            send_whatsapp_text(from_phone, "❌ Could not unlink this WhatsApp right now. Please try again later.")
            return jsonify({"ok": True, "linked": bool(linked_account_id), "mode": "unlink_failed", "unlink": result})

        if lowered in MENU_TRIGGERS:
            send_whatsapp_text(from_phone, _welcome_menu(bool(linked_account_id)))
            return jsonify({"ok": True, "linked": bool(linked_account_id), "mode": "menu"})

        if lowered in {"1", "2", "3", "4", "5", "6", "7"}:
            handled = _handle_menu_option(from_phone, linked_account_id, lowered)
            if handled is not None:
                return handled

        if not linked_account_id:
            if normalized and _is_link_code(normalized):
                attempt = consume_and_link(
                    provider="wa",
                    code=normalized.upper(),
                    provider_user_id=from_phone,
                    display_name=None,
                    phone=from_phone,
                )
                if attempt.get("ok"):
                    send_whatsapp_text(
                        from_phone,
                        "✅ WhatsApp linked successfully!\n"
                        "Now send your tax question here anytime.\n\n"
                        "Reply 7 anytime to see the menu."
                    )
                    return jsonify(
                        {
                            "ok": True,
                            "linked": True,
                            "linked_now": True,
                            "account_id": attempt.get("account_id"),
                        }
                    )

                send_whatsapp_text(from_phone, _link_failure_text(attempt.get("reason") or attempt.get("error")))
                return jsonify({"ok": True, "linked": False, "attempt": attempt})

            _send_onboarding(from_phone)
            return jsonify({"ok": True, "linked": False, "mode": "onboarding"})

        if not normalized:
            send_whatsapp_text(from_phone, _welcome_menu(True))
            return jsonify({"ok": True, "linked": True, "ignored": True, "reason": "no_text"})

        if _is_link_code(normalized):
            send_whatsapp_text(
                from_phone,
                "✅ This WhatsApp number is already linked.\n"
                "Reply 5 if you want to unlink it first."
            )
            return jsonify({"ok": True, "linked": True, "ignored": True, "reason": "already_linked"})

        resp = ask_guarded(
            account_id=linked_account_id,
            question=normalized,
            lang="en",
            channel="whatsapp",
        )
        answer = str(resp.get("answer") or resp.get("message") or "").strip() or "I couldn't process that right now. Please try again."
        send_whatsapp_text(from_phone, answer)
        return jsonify({"ok": True, "linked": True, "account_id": linked_account_id, "ask": resp})

    except Exception as e:
        logging.exception("WA webhook error: %s", e)
        return jsonify({"ok": True})
