# app/routes/telegram.py
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional

from flask import Blueprint, jsonify, request

from app.core.supabase_client import supabase
from app.services.accounts_service import lookup_account, upsert_account
from app.services.ask_service import ask_guarded
from app.services.channel_credit_service import (
    create_credit_payment,
    format_balance_message,
    get_credit_balance,
    get_credit_packages_menu,
    validate_package_number,
)
from app.services.channel_subscription_service import (
    create_subscription_payment,
    format_subscription_message,
    get_plans_list_menu,
    get_user_email,
    has_active_subscription,
    request_email_message,
    validate_plan_number,
)
from app.services.outbound_service import send_telegram_text
from app.services.tax_calculator import calculate_tax
from app.services.tax_filing_service import (
    delete_filing_draft,
    get_user_filings,
    save_filing_draft,
    submit_tax_filing,
)

bp = Blueprint("telegram", __name__)

TELEGRAM_ROUTE_VERSION = "2026-05-26-v34-telegram-account-resolution-health"

LINK_CODE_RE = re.compile(r"^[A-Z0-9]{8}$")
MENU_NUMBER_RE = re.compile(r"^[1-8]$")

# Temporary legacy state store retained from the existing Telegram route.
# A later Telegram batch should move this into a database-backed session table.
user_states: dict[str, dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rows(resp: Any) -> list[dict[str, Any]]:
    data = getattr(resp, "data", None)
    if data is None:
        return []
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def _first(resp: Any) -> Optional[dict[str, Any]]:
    rows = _rows(resp)
    return rows[0] if rows else None


def _safe_exec(builder: Any) -> tuple[bool, Any, Optional[str]]:
    try:
        resp = builder.execute()
        return True, resp, None
    except Exception as exc:
        return False, None, str(exc)


def _env_present(*names: str) -> bool:
    return any(bool(os.getenv(name)) for name in names)


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _parse_amount(text: str) -> float:
    return float(text.replace(",", "").replace("₦", "").strip())


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@bp.route("/telegram/health", methods=["GET", "POST", "HEAD", "OPTIONS"])
def telegram_health():
    return jsonify(
        {
            "ok": True,
            "service": "telegram",
            "version": TELEGRAM_ROUTE_VERSION,
            "route_mount": "/api/telegram/*",
            "account_resolution": "channel_identities_first",
            "configured": {
                "bot_token": _env_present("TELEGRAM_BOT_TOKEN", "TG_BOT_TOKEN", "TELEGRAM_TOKEN"),
                "webhook_secret": _env_present("TELEGRAM_WEBHOOK_SECRET", "TG_WEBHOOK_SECRET"),
            },
        }
    )


# ---------------------------------------------------------------------------
# Account / channel identity resolution
# ---------------------------------------------------------------------------

def _get_telegram_identity(provider_user_id: str) -> Optional[dict[str, Any]]:
    db = supabase()
    ok, resp, _ = _safe_exec(
        db.table("channel_identities")
        .select("*")
        .eq("channel_type", "telegram")
        .eq("provider_user_id", provider_user_id)
        .limit(1)
    )
    if not ok:
        return None
    row = _first(resp)
    if row and row.get("account_id"):
        return row
    return None


def _touch_telegram_identity(identity: dict[str, Any], display_name: Optional[str] = None) -> None:
    identity_id = identity.get("id")
    if not identity_id:
        return

    payload: dict[str, Any] = {"last_seen_at": _utc_now_iso()}
    metadata = identity.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}

    if display_name and not metadata.get("display_name"):
        metadata["display_name"] = display_name
        payload["metadata"] = metadata

    try:
        supabase().table("channel_identities").update(payload).eq("id", identity_id).execute()
    except Exception:
        logging.exception("Telegram identity touch failed")


def _resolve_telegram_account(*, tg_user_id: str, display_name: Optional[str] = None) -> dict[str, Any]:
    """
    Correct account resolution order:
      1. channel_identities first: linked website workspace account.
      2. accounts.provider=tg fallback only when no linked channel exists.
    """
    tg_user_id = _clean_text(tg_user_id)
    if not tg_user_id:
        return {"ok": False, "reason": "missing_tg_user_id"}

    identity = _get_telegram_identity(tg_user_id)
    if identity and identity.get("account_id"):
        _touch_telegram_identity(identity, display_name=display_name)
        return {
            "ok": True,
            "account_id": str(identity.get("account_id")),
            "linked": True,
            "identity": identity,
            "source": "channel_identities",
        }

    try:
        upsert_account(provider="tg", provider_user_id=tg_user_id, display_name=display_name, phone=None)
    except Exception:
        logging.exception("Telegram fallback upsert_account failed")

    try:
        lk = lookup_account(provider="tg", provider_user_id=tg_user_id)
    except Exception as exc:
        logging.exception("Telegram fallback lookup_account failed")
        return {"ok": False, "reason": "lookup_account_failed", "error": str(exc)}

    if not lk.get("ok"):
        return {"ok": False, "reason": "lookup_account_not_ok", "lookup": lk}

    account_id = lk.get("account_id") or lk.get("id") or lk.get("auth_user_id") or tg_user_id

    return {
        "ok": True,
        "account_id": str(account_id),
        "linked": False,
        "identity": None,
        "source": "accounts_fallback",
    }


def _unlink_telegram_identity(tg_user_id: str) -> dict[str, Any]:
    identity = _get_telegram_identity(tg_user_id)
    if not identity:
        return {"ok": True, "unlinked": False, "reason": "not_linked"}

    identity_id = identity.get("id")
    if not identity_id:
        return {"ok": False, "reason": "missing_identity_id"}

    ok, _, err = _safe_exec(supabase().table("channel_identities").delete().eq("id", identity_id))
    if not ok:
        return {"ok": False, "reason": "delete_failed", "error": err}

    return {"ok": True, "unlinked": True, "account_id": identity.get("account_id")}


def _try_consume_link_code(provider_user_id: str, raw_text: str) -> dict[str, Any]:
    code = (raw_text or "").strip().upper()
    if not LINK_CODE_RE.match(code):
        return {"ok": False, "reason": "not_a_code"}

    try:
        res = (
            supabase()
            .rpc(
                "consume_link_token",
                {
                    "p_provider": "tg",
                    "p_code": code,
                    "p_provider_user_id": provider_user_id,
                },
            )
            .execute()
        )
    except Exception as exc:
        return {"ok": False, "reason": "rpc_error", "error": str(exc)}

    row = (res.data or [None])[0]
    if not row:
        return {"ok": False, "reason": "no_rpc_row"}

    linked_account_id = row.get("account_id") or row.get("auth_user_id") or row.get("user_id")
    if row.get("ok") is True and linked_account_id:
        return {"ok": True, "account_id": linked_account_id, "rpc": row}

    return {"ok": False, "reason": row.get("reason") or "consume_failed", "rpc": row}


# ---------------------------------------------------------------------------
# Menus
# ---------------------------------------------------------------------------

def _send_main_menu(chat_id: str, *, linked: bool = False) -> None:
    option_5 = "Unlink website account" if linked else "Link website account"
    menu = (
        "*🤖 Naija Tax Guide*\n\n"
        "Reply with:\n"
        "1️⃣ Ask a tax question\n"
        "2️⃣ Check Usage Credits 💎\n"
        "3️⃣ Check current plan 📌\n"
        "4️⃣ View subscription plans 🛒\n"
        f"5️⃣ {option_5} 🔗\n"
        "6️⃣ Buy Usage Credit add-ons 💳\n"
        "7️⃣ Tax filing & management 🗂️\n"
        "8️⃣ Help / Menu ℹ️\n\n"
        "Quick commands:\n"
        "0 or MENU - Main menu 🏠\n"
        "UNLINK - Disconnect Telegram from website account\n\n"
        "You can also type your Nigerian tax question directly."
    )
    send_telegram_text(chat_id, menu)


def _send_tax_menu(chat_id: str) -> None:
    menu = (
        "*📋 TAX FILING & MANAGEMENT*\n\n"
        "Reply with:\n"
        "P - File PAYE Tax\n"
        "V - File VAT\n"
        "C - File CIT (Company Tax)\n"
        "HISTORY - View my filing history\n"
        "DEADLINES - View tax deadlines\n"
        "BACK - Back to main menu\n\n"
        "Type /paye, /vat, or /cit to start filing."
    )
    send_telegram_text(chat_id, menu)


def _send_help(chat_id: str, *, linked: bool = False) -> None:
    option_5 = "Unlink website account" if linked else "Link website account"
    help_msg = (
        "*📖 Help Guide*\n\n"
        "• Ask tax questions: type your question naturally.\n"
        "  Example: What is PAYE tax?\n\n"
        "• Check Usage Credits: reply 2\n"
        "• View current plan: reply 3\n"
        "• View/upgrade plans: reply 4\n"
        f"• {option_5}: reply 5\n"
        "• Buy Usage Credit add-ons: reply 6\n"
        "• File taxes: reply 7\n"
        "• Show menu: reply 0 or MENU\n\n"
        "Need help? Email support@naijataxguides.com"
    )
    send_telegram_text(chat_id, help_msg)


def _send_welcome(chat_id: str, *, linked: bool = False) -> None:
    send_telegram_text(chat_id, "*Welcome to Naija Tax Guide!* ✅\n\nI'm your AI tax assistant for Nigerian taxes.")
    _send_main_menu(chat_id, linked=linked)


# ---------------------------------------------------------------------------
# Filing flows retained from existing Telegram behavior
# ---------------------------------------------------------------------------

def _handle_paye_filing_step(chat_id: str, account_id: str, user_state: dict[str, Any], text: str) -> bool:
    step = int(user_state.get("step", 1))
    draft = user_state.get("draft", {})
    inputs = draft.get("inputs", {})

    if step == 1:
        try:
            amount = _parse_amount(text)
            inputs["monthly_gross_income"] = amount
            save_filing_draft(account_id, "paye", inputs, [], step + 1)
            user_states[chat_id] = {"filing_type": "paye", "step": 2, "draft": {"inputs": inputs}}
            send_telegram_text(chat_id, f"✅ Received: ₦{amount:,.2f}\n\n📋 Step 2 of 4: Pension Contribution\nEnter your monthly pension contribution, or 0 if none:")
        except ValueError:
            send_telegram_text(chat_id, "❌ Please enter a valid amount. Example: 750000")

    elif step == 2:
        try:
            amount = _parse_amount(text)
            inputs["pension_contribution"] = amount
            save_filing_draft(account_id, "paye", inputs, [], step + 1)
            user_states[chat_id] = {"filing_type": "paye", "step": 3, "draft": {"inputs": inputs}}
            send_telegram_text(chat_id, f"✅ Received: ₦{amount:,.2f}\n\n📋 Step 3 of 4: NHF Contribution\nEnter your NHF contribution, or 0 if none:")
        except ValueError:
            send_telegram_text(chat_id, "❌ Please enter a valid amount.")

    elif step == 3:
        try:
            amount = _parse_amount(text)
            inputs["nhf"] = amount
            save_filing_draft(account_id, "paye", inputs, [], step + 1)
            calc = calculate_tax("paye", inputs)
            monthly_tax = calc.get("monthly_tax_payable", 0)
            annual_tax = calc.get("annual_tax_payable", 0)
            preview = (
                "📋 *PAYE Filing Summary*\n\n"
                f"• Monthly Gross Income: ₦{inputs.get('monthly_gross_income', 0):,.2f}\n"
                f"• Pension Contribution: ₦{inputs.get('pension_contribution', 0):,.2f}\n"
                f"• NHF Contribution: ₦{inputs.get('nhf', 0):,.2f}\n"
                f"• Annual Taxable Income: ₦{calc.get('chargeable_income', 0):,.2f}\n"
                f"• *Annual Tax Payable: ₦{annual_tax:,.2f}*\n"
                f"• *Monthly Tax Deduction: ₦{monthly_tax:,.2f}*\n\n"
                "Reply CONFIRM to submit, or CANCEL to abort."
            )
            user_states[chat_id] = {"filing_type": "paye", "step": 4, "draft": {"inputs": inputs}, "calculation": calc}
            send_telegram_text(chat_id, preview)
        except ValueError:
            send_telegram_text(chat_id, "❌ Please enter a valid amount.")

    elif step == 4:
        if text.lower() == "confirm":
            result = submit_tax_filing(account_id, "paye", inputs, [])
            if result.get("ok"):
                calc = result.get("calculation", {})
                monthly_tax = calc.get("monthly_tax_payable", 0)
                reference = result.get("reference", "N/A")
                submitted_at = result.get("submitted_at", datetime.now().isoformat())
                send_telegram_text(chat_id, f"✅ *PAYE Filing Submitted!*\n\n📋 Reference: {reference}\n📅 Date: {datetime.fromisoformat(submitted_at).strftime('%d %B %Y, %H:%M')}\n💰 Monthly Tax: ₦{monthly_tax:,.2f}\n\nReply HISTORY to see all filings.")
                user_states.pop(chat_id, None)
                delete_filing_draft(account_id, "paye")
            else:
                send_telegram_text(chat_id, f"❌ Filing failed: {result.get('error', 'Unknown error')}")
        elif text.lower() == "cancel":
            delete_filing_draft(account_id, "paye")
            user_states.pop(chat_id, None)
            send_telegram_text(chat_id, "❌ Filing cancelled. Reply MENU to see options.")
        else:
            send_telegram_text(chat_id, "Reply CONFIRM to submit or CANCEL to abort.")

    return True


def _handle_vat_filing_step(chat_id: str, account_id: str, user_state: dict[str, Any], text: str) -> bool:
    step = int(user_state.get("step", 1))
    draft = user_state.get("draft", {})
    inputs = draft.get("inputs", {})

    if step == 1:
        try:
            amount = _parse_amount(text)
            inputs["taxable_supplies"] = amount
            save_filing_draft(account_id, "vat", inputs, [], step + 1)
            user_states[chat_id] = {"filing_type": "vat", "step": 2, "draft": {"inputs": inputs}}
            send_telegram_text(chat_id, f"✅ Received: ₦{amount:,.2f}\n\n📋 Step 2 of 3: Input VAT\nEnter your input VAT, or 0 if none:")
        except ValueError:
            send_telegram_text(chat_id, "❌ Please enter a valid amount.")

    elif step == 2:
        try:
            amount = _parse_amount(text)
            inputs["input_vat"] = amount
            save_filing_draft(account_id, "vat", inputs, [], step + 1)
            calc = calculate_tax("vat", inputs)
            vat_payable = calc.get("vat_payable", 0)
            preview = (
                "📋 *VAT Filing Summary*\n\n"
                f"• Taxable Supplies: ₦{inputs.get('taxable_supplies', 0):,.2f}\n"
                f"• Input VAT: ₦{inputs.get('input_vat', 0):,.2f}\n"
                f"• Output VAT: ₦{calc.get('output_vat', 0):,.2f}\n"
                f"• *VAT Payable: ₦{vat_payable:,.2f}*\n\n"
                "Reply CONFIRM to submit, or CANCEL to abort."
            )
            user_states[chat_id] = {"filing_type": "vat", "step": 3, "draft": {"inputs": inputs}, "calculation": calc}
            send_telegram_text(chat_id, preview)
        except ValueError:
            send_telegram_text(chat_id, "❌ Please enter a valid amount.")

    elif step == 3:
        if text.lower() == "confirm":
            result = submit_tax_filing(account_id, "vat", inputs, [])
            if result.get("ok"):
                calc = result.get("calculation", {})
                vat_payable = calc.get("vat_payable", 0)
                reference = result.get("reference", "N/A")
                submitted_at = result.get("submitted_at", datetime.now().isoformat())
                send_telegram_text(chat_id, f"✅ *VAT Filing Submitted!*\n\n📋 Reference: {reference}\n📅 Date: {datetime.fromisoformat(submitted_at).strftime('%d %B %Y, %H:%M')}\n💰 VAT Payable: ₦{vat_payable:,.2f}\n\nReply HISTORY to see all filings.")
                user_states.pop(chat_id, None)
                delete_filing_draft(account_id, "vat")
            else:
                send_telegram_text(chat_id, f"❌ Filing failed: {result.get('error', 'Unknown error')}")
        elif text.lower() == "cancel":
            delete_filing_draft(account_id, "vat")
            user_states.pop(chat_id, None)
            send_telegram_text(chat_id, "❌ Filing cancelled. Reply MENU to see options.")
        else:
            send_telegram_text(chat_id, "Reply CONFIRM to submit or CANCEL to abort.")

    return True


def _handle_cit_filing_step(chat_id: str, account_id: str, user_state: dict[str, Any], text: str) -> bool:
    step = int(user_state.get("step", 1))
    draft = user_state.get("draft", {})
    inputs = draft.get("inputs", {})

    if step == 1:
        try:
            amount = _parse_amount(text)
            inputs["gross_profit"] = amount
            save_filing_draft(account_id, "cit", inputs, [], step + 1)
            user_states[chat_id] = {"filing_type": "cit", "step": 2, "draft": {"inputs": inputs}}
            send_telegram_text(chat_id, f"✅ Received: ₦{amount:,.2f}\n\n📋 Step 2 of 3: Allowable Expenses\nEnter your allowable expenses:")
        except ValueError:
            send_telegram_text(chat_id, "❌ Please enter a valid amount.")

    elif step == 2:
        try:
            amount = _parse_amount(text)
            inputs["allowable_expenses"] = amount
            save_filing_draft(account_id, "cit", inputs, [], step + 1)
            calc = calculate_tax("cit", inputs)
            cit_payable = calc.get("cit_payable", 0)
            company_size = calc.get("company_size", "N/A")
            rate = calc.get("applicable_rate", 0)
            preview = (
                "📋 *CIT Filing Summary*\n\n"
                f"• Gross Profit: ₦{inputs.get('gross_profit', 0):,.2f}\n"
                f"• Allowable Expenses: ₦{inputs.get('allowable_expenses', 0):,.2f}\n"
                f"• Assessable Profit: ₦{calc.get('assessable_profit', 0):,.2f}\n"
                f"• Company Size: {str(company_size).title()}\n"
                f"• Applicable Rate: {rate}%\n"
                f"• *CIT Payable: ₦{cit_payable:,.2f}*\n\n"
                "Reply CONFIRM to submit, or CANCEL to abort."
            )
            user_states[chat_id] = {"filing_type": "cit", "step": 3, "draft": {"inputs": inputs}, "calculation": calc}
            send_telegram_text(chat_id, preview)
        except ValueError:
            send_telegram_text(chat_id, "❌ Please enter a valid amount.")

    elif step == 3:
        if text.lower() == "confirm":
            result = submit_tax_filing(account_id, "cit", inputs, [])
            if result.get("ok"):
                calc = result.get("calculation", {})
                cit_payable = calc.get("cit_payable", 0)
                reference = result.get("reference", "N/A")
                submitted_at = result.get("submitted_at", datetime.now().isoformat())
                send_telegram_text(chat_id, f"✅ *CIT Filing Submitted!*\n\n📋 Reference: {reference}\n📅 Date: {datetime.fromisoformat(submitted_at).strftime('%d %B %Y, %H:%M')}\n💰 CIT Payable: ₦{cit_payable:,.2f}\n\nReply HISTORY to see all filings.")
                user_states.pop(chat_id, None)
                delete_filing_draft(account_id, "cit")
            else:
                send_telegram_text(chat_id, f"❌ Filing failed: {result.get('error', 'Unknown error')}")
        elif text.lower() == "cancel":
            delete_filing_draft(account_id, "cit")
            user_states.pop(chat_id, None)
            send_telegram_text(chat_id, "❌ Filing cancelled. Reply MENU to see options.")
        else:
            send_telegram_text(chat_id, "Reply CONFIRM to submit or CANCEL to abort.")

    return True


def _handle_continue_filing(chat_id: str, account_id: str, text: str) -> bool:
    user_state = user_states.get(chat_id, {})
    filing_type = user_state.get("filing_type")
    if filing_type == "paye":
        return _handle_paye_filing_step(chat_id, account_id, user_state, text)
    if filing_type == "vat":
        return _handle_vat_filing_step(chat_id, account_id, user_state, text)
    if filing_type == "cit":
        return _handle_cit_filing_step(chat_id, account_id, user_state, text)
    return False


def _handle_tax_filing_command(chat_id: str, account_id: str, text: str) -> bool:
    text_lower = text.lower().strip()

    if text_lower in ["/paye", "file paye", "file paye tax", "paye", "p"]:
        user_states[chat_id] = {"filing_type": "paye", "step": 1, "draft": {"inputs": {}}}
        send_telegram_text(chat_id, "📋 *PAYE Tax Filing - Step 1 of 4*\n\nPlease provide your monthly gross income.\nExample: 750000")
        return True
    if text_lower in ["/vat", "file vat", "file vat tax", "vat", "v"]:
        user_states[chat_id] = {"filing_type": "vat", "step": 1, "draft": {"inputs": {}}}
        send_telegram_text(chat_id, "📋 *VAT Filing - Step 1 of 3*\n\nEnter your total taxable supplies for the period.\nExample: 5000000")
        return True
    if text_lower in ["/cit", "file cit", "file cit tax", "file company tax", "cit", "c"]:
        user_states[chat_id] = {"filing_type": "cit", "step": 1, "draft": {"inputs": {}}}
        send_telegram_text(chat_id, "📋 *CIT Filing - Step 1 of 3*\n\nEnter your gross profit for the period.\nExample: 10000000")
        return True
    if text_lower in ["/history", "history", "my filings", "filing history"]:
        filings = get_user_filings(account_id, limit=10)
        if filings:
            msg = "📋 *Your Tax Filings*\n\n"
            for item in filings[:5]:
                msg += f"• *{item.get('tax_type', '').upper()}*: {item.get('reference', 'N/A')}\n"
                msg += f"  Status: {item.get('status', 'N/A')}\n"
                msg += f"  Date: {item.get('submitted_at', '')[:10] if item.get('submitted_at') else 'N/A'}\n\n"
            if len(filings) > 5:
                msg += f"\n+ {len(filings) - 5} more. Visit web for full history."
            send_telegram_text(chat_id, msg)
        else:
            send_telegram_text(chat_id, "📋 No tax filings found. Reply P to file PAYE tax.")
        return True
    if text_lower in ["/deadlines", "deadlines", "tax deadlines", "filing deadlines"]:
        send_telegram_text(chat_id, "📅 *Tax Deadlines*\n\n• PAYE: Monthly by 10th\n• VAT: Monthly by 21st\n• CIT: 6 months after year end\n• Annual Returns: March 31st\n\nSet reminders in your web dashboard.")
        return True
    return False


# ---------------------------------------------------------------------------
# Telegram webhook
# ---------------------------------------------------------------------------

@bp.route("/telegram/webhook", methods=["POST"])
def tg_webhook():
    update = request.get_json(silent=True) or {}

    if update.get("callback_query"):
        return jsonify({"ok": True, "ignored": True, "type": "callback_query"})

    msg = update.get("message") or update.get("edited_message") or {}
    if not msg:
        return jsonify({"ok": True, "ignored": True, "type": "no_message"})

    chat = msg.get("chat") or {}
    chat_id_str = str(chat.get("id") or "").strip()
    text = (msg.get("text") or "").strip()
    text_lower = text.lower().strip()

    user = msg.get("from") or {}
    tg_user_id = str(user.get("id") or "").strip()
    display_name = " ".join([x for x in [user.get("first_name"), user.get("last_name")] if x]) or None

    if not tg_user_id or not chat_id_str:
        return jsonify({"ok": True, "ignored": True, "type": "missing_identity"})

    resolved = _resolve_telegram_account(tg_user_id=tg_user_id, display_name=display_name)
    if not resolved.get("ok"):
        send_telegram_text(chat_id_str, "System error. Please try again.")
        return jsonify({"ok": True, "resolved": False, "reason": resolved.get("reason")})

    account_id = str(resolved.get("account_id"))
    linked = bool(resolved.get("linked"))
    user_state = user_states.get(chat_id_str, {})

    if not text:
        _send_welcome(chat_id_str, linked=linked)
        return jsonify({"ok": True})

    if text_lower in ["/start", "start", "0", "menu", "/menu"]:
        user_states.pop(chat_id_str, None)
        _send_main_menu(chat_id_str, linked=linked)
        return jsonify({"ok": True})

    if text_lower in ["help", "/help", "?"]:
        _send_help(chat_id_str, linked=linked)
        return jsonify({"ok": True})

    if text_lower in ["back", "cancel"]:
        if user_state:
            user_states.pop(chat_id_str, None)
            send_telegram_text(chat_id_str, "Current flow cancelled.")
        _send_main_menu(chat_id_str, linked=linked)
        return jsonify({"ok": True})

    if text_lower in ["unlink", "unlink telegram", "disconnect telegram", "remove telegram"]:
        result = _unlink_telegram_identity(tg_user_id)
        user_states.pop(chat_id_str, None)
        if result.get("ok") and result.get("unlinked"):
            send_telegram_text(chat_id_str, "✅ Telegram unlinked successfully.\n\nThis Telegram account is no longer connected to your website workspace. Reply 5 anytime to link again.")
        elif result.get("ok"):
            send_telegram_text(chat_id_str, "Telegram is not currently linked to a website account.\n\nReply 5 to get linking instructions.")
        else:
            send_telegram_text(chat_id_str, "❌ Telegram unlink failed. Please try again or use the Channels page on the website.")
        return jsonify({"ok": True, "unlink": result})

    if user_state.get("awaiting_email"):
        email = text.strip().lower()
        pending_plan = user_state.get("pending_plan")
        if email in ["cancel", "0", "menu"]:
            user_states.pop(chat_id_str, None)
            send_telegram_text(chat_id_str, "❌ Subscription cancelled. Reply 4 to see plans.")
            return jsonify({"ok": True})
        if "@" in email and "." in email:
            result = create_subscription_payment(account_id=account_id, plan=pending_plan, channel_type="telegram", provider_user_id=tg_user_id, email=email)
            send_telegram_text(chat_id_str, result.get("message") if result.get("ok") else f"❌ {result.get('message', 'Please try again.')}")
            user_states.pop(chat_id_str, None)
        else:
            send_telegram_text(chat_id_str, "❌ Invalid email. Send a valid email or CANCEL to abort.")
        return jsonify({"ok": True})

    if user_state.get("filing_type") and user_state.get("step"):
        _handle_continue_filing(chat_id_str, account_id, text)
        return jsonify({"ok": True})

    if _handle_tax_filing_command(chat_id_str, account_id, text):
        return jsonify({"ok": True})

    has_subscription = bool(has_active_subscription(account_id))

    if MENU_NUMBER_RE.match(text):
        option = int(text)
        if option == 1:
            send_telegram_text(chat_id_str, "💬 Please type your Nigerian tax question.")
            return jsonify({"ok": True})
        if option == 2:
            balance = get_credit_balance(account_id)
            send_telegram_text(chat_id_str, format_balance_message(balance))
            return jsonify({"ok": True})
        if option == 3:
            send_telegram_text(chat_id_str, format_subscription_message(account_id))
            return jsonify({"ok": True})
        if option == 4:
            send_telegram_text(chat_id_str, get_plans_list_menu())
            return jsonify({"ok": True})
        if option == 5:
            if linked:
                send_telegram_text(chat_id_str, "🔗 *Telegram is linked*\n\nThis Telegram account is already connected to your website workspace.\n\nReply UNLINK to disconnect Telegram from the website account, or reply 0 for main menu.")
            else:
                send_telegram_text(chat_id_str, "🔗 *Link Telegram to Website*\n\n1. Login on the website.\n2. Open Channels.\n3. Generate a Telegram link code.\n4. Send that 8-character code here.\n\nAfter linking, Telegram will use the same website plan and Usage Credits.")
            return jsonify({"ok": True})
        if option == 6:
            if has_subscription:
                send_telegram_text(chat_id_str, get_credit_packages_menu())
            else:
                send_telegram_text(chat_id_str, "💎 Usage Credit add-ons are available only to active paid subscribers.\n\nReply 4 to view subscription plans or 3 to check your current plan.")
            return jsonify({"ok": True})
        if option == 7:
            _send_tax_menu(chat_id_str)
            return jsonify({"ok": True})
        if option == 8:
            _send_help(chat_id_str, linked=linked)
            return jsonify({"ok": True})

    if text_lower in ["t10", "t50", "t100", "t500"]:
        if not has_subscription:
            send_telegram_text(chat_id_str, "💎 Usage Credit add-ons are available only to active paid subscribers.\n\nReply 4 to view subscription plans.")
            return jsonify({"ok": True})
        package_map = {"t10": 1, "t50": 2, "t100": 3, "t500": 4}
        package_num = package_map[text_lower]
        package = validate_package_number(package_num)
        if not package:
            send_telegram_text(chat_id_str, "❌ Invalid add-on package. Reply 6 to see packages.")
            return jsonify({"ok": True})
        result = create_credit_payment(account_id, package_num, "telegram", tg_user_id)
        send_telegram_text(chat_id_str, result.get("message") if result.get("ok") else f"❌ {result.get('message', 'Please try again.')}")
        return jsonify({"ok": True})

    if re.match(r"^s[1-9]$", text_lower):
        plan_num = int(text_lower[1:])
        plan = validate_plan_number(plan_num)
        if not plan:
            send_telegram_text(chat_id_str, "❌ Invalid plan code. Reply 4 to see plans.")
            return jsonify({"ok": True})
        user_email = get_user_email(account_id)
        if user_email:
            result = create_subscription_payment(account_id=account_id, plan=plan, channel_type="telegram", provider_user_id=tg_user_id, email=user_email)
            send_telegram_text(chat_id_str, result.get("message") if result.get("ok") else f"❌ {result.get('message', 'Please try again.')}")
        else:
            user_states[chat_id_str] = {"awaiting_email": True, "pending_plan": plan}
            send_telegram_text(chat_id_str, request_email_message())
        return jsonify({"ok": True})

    if LINK_CODE_RE.match(text.upper()):
        attempt = _try_consume_link_code(tg_user_id, text)
        if attempt.get("ok"):
            send_telegram_text(chat_id_str, "✅ *Telegram linked successfully!*\n\nYour Telegram account is now connected to the website workspace. Your plan and Usage Credits will now sync here.")
            return jsonify({"ok": True, "linked": True, "account_id": attempt.get("account_id")})
        send_telegram_text(chat_id_str, "❌ *Invalid or expired link code*\n\nPlease generate a fresh Telegram code from the website Channels page and send it here.\n\nReply 0 for main menu.")
        return jsonify({"ok": True, "linked": False, "reason": attempt.get("reason")})

    try:
        result = ask_guarded({"question": text, "account_id": account_id, "lang": "en", "channel": "telegram"})
        if result.get("ok"):
            answer = result.get("answer", "")
            send_telegram_text(chat_id_str, answer if answer else "I couldn't find an answer. Please try rephrasing.\n\nReply 0 for main menu.")
        else:
            send_telegram_text(chat_id_str, "Sorry, I encountered an error. Please try again.\n\nReply 0 for main menu.")
        return jsonify({"ok": True, "answered": True, "account_source": resolved.get("source")})
    except Exception as exc:
        logging.exception("Telegram webhook error: %s", exc)
        send_telegram_text(chat_id_str, "Sorry, I encountered an error. Please try again later.")
        return jsonify({"ok": True, "error_handled": True})
