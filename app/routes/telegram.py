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

# Batch 27B1 fix: use the imported Supabase client object directly; do not call supabase().

bp = Blueprint("telegram", __name__)

TELEGRAM_ROUTE_VERSION = "2026-05-26-v34d-telegram-format-state-cleanup"

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
            "account_resolution": "channel_identities_first_accounts_auth_fallback",
            "command_namespace": "ALL_CR_PAY_ACC_SET",
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
    db = supabase
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


def _get_telegram_account_row(provider_user_id: str) -> Optional[dict[str, Any]]:
    provider_user_id = _clean_text(provider_user_id)
    if not provider_user_id:
        return None

    try:
        resp = (
            supabase.table("accounts")
            .select("*")
            .eq("provider", "tg")
            .eq("provider_user_id", provider_user_id)
            .limit(1)
            .execute()
        )
        return _first(resp)
    except Exception:
        logging.exception("Telegram account row lookup failed")
        return None


def _effective_account_id_from_tg_account(row: Optional[dict[str, Any]]) -> Optional[str]:
    """
    For provider=tg rows:
      - auth_user_id is the linked website owner account.
      - account_id/id can be only the standalone Telegram shell account.

    Therefore auth_user_id must be preferred whenever it exists.
    """
    if not isinstance(row, dict):
        return None

    for key in ("auth_user_id", "account_id", "id"):
        value = _clean_text(row.get(key))
        if value:
            return value

    return None


def _clear_telegram_account_auth(provider_user_id: str) -> None:
    provider_user_id = _clean_text(provider_user_id)
    if not provider_user_id:
        return

    try:
        supabase.table("accounts").update(
            {
                "auth_user_id": None,
                "updated_at": _utc_now_iso(),
            }
        ).eq("provider", "tg").eq("provider_user_id", provider_user_id).execute()
    except Exception:
        logging.exception("Telegram accounts.auth_user_id clear failed")


def _set_telegram_account_auth(provider_user_id: str, account_id: str, display_name: Optional[str] = None) -> None:
    provider_user_id = _clean_text(provider_user_id)
    account_id = _clean_text(account_id)
    if not provider_user_id or not account_id:
        return

    try:
        # Ensure the provider=tg row exists first.
        try:
            upsert_account(provider="tg", provider_user_id=provider_user_id, display_name=display_name, phone=None)
        except Exception:
            logging.exception("Telegram account upsert before auth link failed")

        patch: dict[str, Any] = {
            "auth_user_id": account_id,
            "updated_at": _utc_now_iso(),
        }
        if display_name:
            patch["display_name"] = display_name

        supabase.table("accounts").update(patch).eq("provider", "tg").eq("provider_user_id", provider_user_id).execute()
    except Exception:
        logging.exception("Telegram accounts.auth_user_id set failed")


def _ensure_telegram_channel_identity(
    *,
    account_id: str,
    provider_user_id: str,
    display_name: Optional[str] = None,
    source: str = "telegram_link_persistence",
) -> dict[str, Any]:
    """
    Persist the durable link in channel_identities.

    The old RPC/account fallback can report "linked successfully" without a
    channel_identities row. The web Channels page and the Telegram resolver
    both need channel_identities, so we create it here after successful code
    consumption.
    """
    account_id = _clean_text(account_id)
    provider_user_id = _clean_text(provider_user_id)

    if not account_id or not provider_user_id:
        return {"ok": False, "reason": "missing_account_or_provider_user_id"}

    now = _utc_now_iso()

    # Enforce one Telegram identity per Telegram user and one Telegram identity per account.
    for field, value in (("provider_user_id", provider_user_id), ("account_id", account_id)):
        try:
            supabase.table("channel_identities").delete().eq("channel_type", "telegram").eq(field, value).execute()
        except Exception:
            logging.exception("Telegram channel identity cleanup failed for %s=%s", field, value)

    base_payload: dict[str, Any] = {
        "account_id": account_id,
        "channel_type": "telegram",
        "provider_user_id": provider_user_id,
    }

    metadata = {
        "source": source,
        "display_name": display_name,
        "linked_at": now,
    }

    # Column-safe attempts. Different table revisions may not have every column.
    payload_attempts: list[dict[str, Any]] = [
        {
            **base_payload,
            "is_verified": True,
            "verified": True,
            "value": provider_user_id,
            "metadata": metadata,
            "created_at": now,
            "updated_at": now,
            "last_seen_at": now,
        },
        {
            **base_payload,
            "is_verified": True,
            "metadata": metadata,
            "updated_at": now,
            "last_seen_at": now,
        },
        {
            **base_payload,
            "metadata": metadata,
            "updated_at": now,
            "last_seen_at": now,
        },
        base_payload,
    ]

    last_error = None
    for payload in payload_attempts:
        ok, resp, err = _safe_exec(supabase.table("channel_identities").insert(payload))
        if ok:
            row = _first(resp)
            return {"ok": True, "row": row, "payload_keys": sorted(payload.keys())}
        last_error = err

    return {"ok": False, "reason": "insert_failed", "error": last_error}


def _persist_successful_telegram_link(
    *,
    account_id: str,
    provider_user_id: str,
    display_name: Optional[str] = None,
) -> dict[str, Any]:
    account_id = _clean_text(account_id)
    provider_user_id = _clean_text(provider_user_id)

    if not account_id or not provider_user_id:
        return {"ok": False, "reason": "missing_account_or_provider_user_id"}

    _set_telegram_account_auth(provider_user_id, account_id, display_name=display_name)
    identity_result = _ensure_telegram_channel_identity(
        account_id=account_id,
        provider_user_id=provider_user_id,
        display_name=display_name,
        source="consume_link_token_success",
    )

    return {
        "ok": bool(identity_result.get("ok")),
        "account_id": account_id,
        "provider_user_id": provider_user_id,
        "identity_result": identity_result,
    }


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
        supabase.table("channel_identities").update(payload).eq("id", identity_id).execute()
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

    row = lk.get("row") if isinstance(lk.get("row"), dict) else _get_telegram_account_row(tg_user_id)
    auth_user_id = _clean_text((row or {}).get("auth_user_id") or lk.get("auth_user_id"))

    # If auth_user_id exists, this Telegram row is already linked to a website account.
    # Prefer it over the Telegram shell account_id/id.
    if auth_user_id:
        identity_result = _ensure_telegram_channel_identity(
            account_id=auth_user_id,
            provider_user_id=tg_user_id,
            display_name=display_name,
            source="accounts_auth_user_id_fallback",
        )
        return {
            "ok": True,
            "account_id": auth_user_id,
            "linked": True,
            "identity": identity_result.get("row"),
            "source": "accounts_auth_user_id_fallback",
        }

    account_id = _effective_account_id_from_tg_account(row) or lk.get("account_id") or lk.get("id") or tg_user_id

    return {
        "ok": True,
        "account_id": str(account_id),
        "linked": False,
        "identity": None,
        "source": "accounts_fallback",
    }


def _unlink_telegram_identity(tg_user_id: str) -> dict[str, Any]:
    identity = _get_telegram_identity(tg_user_id)
    unlinked = False
    account_id = None

    if identity:
        identity_id = identity.get("id")
        account_id = identity.get("account_id")
        if identity_id:
            ok, _, err = _safe_exec(supabase.table("channel_identities").delete().eq("id", identity_id))
            if not ok:
                return {"ok": False, "reason": "delete_failed", "error": err}
            unlinked = True

    # Also clear the legacy accounts.auth_user_id fallback so a Telegram account
    # cannot remain silently linked after channel_identities is removed.
    tg_row = _get_telegram_account_row(tg_user_id)
    if tg_row and _clean_text(tg_row.get("auth_user_id")):
        account_id = account_id or tg_row.get("auth_user_id")
        _clear_telegram_account_auth(tg_user_id)
        unlinked = True

    if not unlinked:
        return {"ok": True, "unlinked": False, "reason": "not_linked"}

    return {"ok": True, "unlinked": True, "account_id": account_id}


def _try_consume_link_code(provider_user_id: str, raw_text: str, display_name: Optional[str] = None) -> dict[str, Any]:
    code = (raw_text or "").strip().upper()
    if not LINK_CODE_RE.match(code):
        return {"ok": False, "reason": "not_a_code"}

    try:
        res = (
            supabase
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
        persist_result = _persist_successful_telegram_link(
            account_id=str(linked_account_id),
            provider_user_id=provider_user_id,
            display_name=display_name,
        )
        if not persist_result.get("ok"):
            return {
                "ok": False,
                "reason": "link_persistence_failed",
                "account_id": linked_account_id,
                "rpc": row,
                "persist_result": persist_result,
            }
        return {"ok": True, "account_id": linked_account_id, "rpc": row, "persist_result": persist_result}

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
        "ALL - Show all Telegram commands\n"
        "CR1 - Credit balance\n"
        "PAY1 - Current plan\n"
        "ACC1 - Account/channel status\n"
        "SET1 - Settings help\n"
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
        "• Check Usage Credits: reply 2 or CR1\n"
        "• View current plan: reply 3 or PAY1\n"
        "• View/upgrade plans: reply 4 or PAY2\n"
        f"• {option_5}: reply 5 or ACC2\n"
        "• Buy Usage Credit add-ons: reply 6 or CR2\n"
        "• File taxes: reply 7\n"
        "• Show all commands: reply ALL\n"
        "• Show menu: reply 0 or MENU\n\n"
        "Need help? Email support@naijataxguides.com"
    )
    send_telegram_text(chat_id, help_msg)


def _send_welcome(chat_id: str, *, linked: bool = False) -> None:
    send_telegram_text(chat_id, "*Welcome to Naija Tax Guide!* ✅\n\nI'm your AI tax assistant for Nigerian taxes.")
    _send_main_menu(chat_id, linked=linked)



# ---------------------------------------------------------------------------
# Batch 27C command namespace helpers
# ---------------------------------------------------------------------------

def _money(value: Any) -> str:
    try:
        amount = float(value or 0)
        return f"₦{amount:,.0f}"
    except Exception:
        return str(value or "₦0")


def _date_short(value: Any) -> str:
    text = _clean_text(value)
    if not text:
        return "Not shown"

    try:
        cleaned = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        return dt.strftime("%d %b %Y")
    except Exception:
        return text[:10]


def _send_all_commands(chat_id: str, *, linked: bool = False) -> None:
    option_5 = "UNLINK" if linked else "5 / link code"

    msg = (
        "*📚 Telegram Command Center*\n\n"
        "*Main menu*\n"
        "0 or MENU - Main menu\n"
        "1 - Ask a tax question\n"
        "2 - Credit balance\n"
        "3 - Current plan\n"
        "4 - Subscription plans\n"
        f"5 - Link / Unlink ({option_5})\n"
        "6 - Usage Credit add-ons\n"
        "7 - Tax filing menu\n"
        "8 - Help\n\n"
        "*Credits*\n"
        "CR1 - Credit balance\n"
        "CR2 - Buy Usage Credit add-ons\n"
        "CR3 - Credit deduction/activity log\n"
        "CR4 - Credit rules and help\n\n"
        "*Billing / Plans*\n"
        "PAY1 - Current plan\n"
        "PAY2 - View subscription plans\n"
        "PAY3 - How to upgrade or renew\n"
        "PAY4 - Payment history\n"
        "PAY5 - Renewal/cancel information\n"
        "PAY6 - Billing support\n\n"
        "*Account / Channels*\n"
        "ACC1 - Account and channel status\n"
        "ACC2 - Link/unlink instructions\n"
        "ACC3 - Support contact\n\n"
        "*Settings*\n"
        "SET1 - Language settings\n"
        "SET2 - Notification settings\n"
        "SET3 - Privacy and account safety\n\n"
        "Use CANCEL anytime to stop the current flow."
    )
    send_telegram_text(chat_id, msg)


def _send_credit_package_menu(chat_id: str, account_id: str, *, has_subscription: bool) -> None:
    if not has_subscription:
        send_telegram_text(
            chat_id,
            "💎 Usage Credit add-ons are available only to active paid subscribers.\n\n"
            "Reply PAY2 to view subscription plans or PAY1 to check your current plan.",
        )
        return

    msg = (
        "*💎 Buy Usage Credit Add-ons*\n\n"
        "Reply with a package command:\n\n"
        "T10 - 10 credits - ₦500\n"
        "T50 - 50 credits - ₦2,000\n"
        "T100 - 100 credits - ₦3,500\n"
        "T500 - 500 credits - ₦15,000\n\n"
        "Reply T10, T50, T100, or T500.\n"
        "Reply 0 or CANCEL to stop."
    )
    user_states[chat_id] = {"awaiting_credit_package": True}
    send_telegram_text(chat_id, msg)


def _select_credit_package_number(text_lower: str) -> Optional[int]:
    package_map = {
        "t10": 1,
        "t50": 2,
        "t100": 3,
        "t500": 4,
        "cr2a": 1,
        "cr2b": 2,
        "cr2c": 3,
        "cr2d": 4,
    }
    return package_map.get(text_lower)


def _handle_credit_package_selection(
    *,
    chat_id: str,
    account_id: str,
    tg_user_id: str,
    text_lower: str,
    has_subscription: bool,
) -> bool:
    package_num = _select_credit_package_number(text_lower)

    if package_num is None:
        return False

    if not has_subscription:
        user_states.pop(chat_id, None)
        send_telegram_text(
            chat_id,
            "💎 Usage Credit add-ons are available only to active paid subscribers.\n\n"
            "Reply PAY2 to view subscription plans.",
        )
        return True

    package = validate_package_number(package_num)
    if not package:
        send_telegram_text(chat_id, "❌ Invalid add-on package. Reply CR2 to see packages again.")
        return True

    result = create_credit_payment(account_id, package_num, "telegram", tg_user_id)
    user_states.pop(chat_id, None)
    send_telegram_text(
        chat_id,
        result.get("message") if result.get("ok") else f"❌ {result.get('message', 'Please try again.')}",
    )
    return True


def _safe_table_rows(table_name: str, account_id: str, limit: int = 5) -> list[dict[str, Any]]:
    try:
        resp = (
            supabase.table(table_name)
            .select("*")
            .eq("account_id", account_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return _rows(resp)
    except Exception:
        return []


def _send_credit_activity(chat_id: str, account_id: str) -> None:
    rows: list[dict[str, Any]] = []

    for table_name in ("ai_credit_transactions", "credit_transactions", "ai_credit_deductions"):
        rows = _safe_table_rows(table_name, account_id, limit=5)
        if rows:
            break

    balance = get_credit_balance(account_id)

    if not rows:
        bal = balance.get("balance", 0) if isinstance(balance, dict) else "Not shown"
        send_telegram_text(
            chat_id,
            "*📉 Usage Credit Activity*\n\n"
            "No recent credit deduction log found yet.\n\n"
            f"Current balance: {bal}\n\n"
            "Reply CR1 for balance, CR2 to buy add-ons, or 0 for main menu.",
        )
        return

    msg = "*📉 Recent Usage Credit Activity*\n\n"
    for idx, row in enumerate(rows, 1):
        amount = row.get("amount") or row.get("credits") or row.get("credit_delta") or row.get("delta") or row.get("used") or ""
        reason = row.get("reason") or row.get("description") or row.get("event_type") or row.get("type") or "Credit activity"
        created_at = row.get("created_at") or row.get("updated_at")
        msg += f"{idx}. {reason}\n"
        if amount != "":
            msg += f"   Credits: {amount}\n"
        msg += f"   Date: {_date_short(created_at)}\n\n"

    msg += "Reply CR1 for balance, CR2 to buy add-ons, or 0 for main menu."
    send_telegram_text(chat_id, msg)


def _send_credit_rules(chat_id: str) -> None:
    send_telegram_text(
        chat_id,
        "*💎 Usage Credit Rules*\n\n"
        "• Credits are shared across web, WhatsApp, and Telegram when your channels are linked.\n"
        "• AI tax answers and premium quiz explanations may deduct credits.\n"
        "• Basic calculators and free tools should remain available according to your plan rules.\n"
        "• Add-ons are available only to active paid subscribers.\n\n"
        "Reply CR1 for balance, CR2 for add-ons, or 0 for main menu.",
    )


def _send_payment_history(chat_id: str, account_id: str) -> None:
    rows: list[dict[str, Any]] = []

    for table_name in ("paystack_transactions", "payment_transactions", "billing_transactions"):
        rows = _safe_table_rows(table_name, account_id, limit=5)
        if rows:
            break

    if not rows:
        send_telegram_text(
            chat_id,
            "*🧾 Payment History*\n\n"
            "No payment history found for this account yet.\n\n"
            "Reply PAY2 to view plans or PAY6 for billing support.",
        )
        return

    msg = "*🧾 Recent Payment History*\n\n"
    for idx, row in enumerate(rows, 1):
        plan = row.get("plan_code") or row.get("plan") or row.get("product_code") or "Payment"
        status = row.get("status") or row.get("payment_status") or row.get("event") or "status not shown"
        amount = row.get("amount") or row.get("amount_naira") or row.get("price") or row.get("paid_amount")
        reference = row.get("reference") or row.get("payment_reference") or row.get("provider_reference") or ""
        created_at = row.get("created_at") or row.get("paid_at") or row.get("updated_at")

        msg += f"{idx}. {plan}\n"
        if amount is not None:
            msg += f"   Amount: {_money(amount)}\n"
        msg += f"   Status: {status}\n"
        if reference:
            msg += f"   Ref: {reference}\n"
        msg += f"   Date: {_date_short(created_at)}\n\n"

    msg += "Reply PAY1 for current plan, PAY2 for plans, or 0 for main menu."
    send_telegram_text(chat_id, msg)


def _send_upgrade_help(chat_id: str) -> None:
    send_telegram_text(
        chat_id,
        "*🛒 Upgrade / Renew Help*\n\n"
        "1. Reply PAY2 to view available plans.\n"
        "2. Choose a plan using S1, S2, S3, etc.\n"
        "3. Complete payment through the secure checkout link.\n"
        "4. Your web, WhatsApp, and Telegram access should update automatically after payment.\n\n"
        "Reply PAY1 to check your current plan or PAY6 for billing support.",
    )


def _send_renewal_help(chat_id: str) -> None:
    send_telegram_text(
        chat_id,
        "*🔁 Renewal / Cancel Information*\n\n"
        "Your current plan details are shown with PAY1.\n\n"
        "To upgrade or renew, reply PAY2 and select a plan.\n"
        "To cancel or resolve billing issues, contact support.\n\n"
        "Support: support@naijataxguides.com",
    )


def _send_billing_support(chat_id: str) -> None:
    send_telegram_text(
        chat_id,
        "*🧾 Billing Support*\n\n"
        "For failed payment, wrong plan, missing credits, or subscription issues, contact:\n"
        "support@naijataxguides.com\n\n"
        "Include your registered email/phone and payment reference if available.\n\n"
        "Reply PAY4 for payment history or 0 for main menu.",
    )


def _send_account_status(chat_id: str, account_id: str, tg_user_id: str, linked: bool) -> None:
    identity = _get_telegram_identity(tg_user_id)
    balance = get_credit_balance(account_id)

    msg = (
        "*👤 Account / Channel Status*\n\n"
        f"Telegram linked: {'Yes ✅' if linked else 'No ❌'}\n"
        f"Telegram ID: {tg_user_id}\n"
        f"Workspace account: {account_id}\n"
    )

    if identity:
        msg += f"Linked account: {identity.get('account_id') or account_id}\n"
        msg += f"Last seen: {_date_short(identity.get('last_seen_at') or identity.get('updated_at'))}\n"

    if isinstance(balance, dict):
        msg += f"Usage Credits: {balance.get('balance', 0)}\n"

    msg += "\nReply ACC2 for link/unlink help or 0 for main menu."
    send_telegram_text(chat_id, msg)


def _send_link_help(chat_id: str, *, linked: bool) -> None:
    if linked:
        send_telegram_text(
            chat_id,
            "*🔗 Telegram is linked*\n\n"
            "This Telegram account is connected to your website workspace.\n\n"
            "Reply UNLINK to disconnect Telegram, or reply 0 for main menu.",
        )
    else:
        send_telegram_text(
            chat_id,
            "*🔗 Link Telegram to Website*\n\n"
            "1. Login on the website.\n"
            "2. Open Channels.\n"
            "3. Generate a Telegram link code.\n"
            "4. Send the 8-character code here.\n\n"
            "After linking, Telegram will use the same website plan and Usage Credits.",
        )


def _send_account_support(chat_id: str) -> None:
    send_telegram_text(
        chat_id,
        "*🛟 Account Support*\n\n"
        "For login, channel linking, wrong balance, or account access issues, contact:\n"
        "support@naijataxguides.com\n\n"
        "Useful commands:\n"
        "ACC1 - Account/channel status\n"
        "ACC2 - Link/unlink help\n"
        "CR1 - Credit balance\n"
        "PAY1 - Current plan",
    )


def _send_language_settings(chat_id: str) -> None:
    send_telegram_text(
        chat_id,
        "*🌐 Language Settings*\n\n"
        "Current default language: English.\n\n"
        "You can ask tax questions in simple English. Multi-language preferences can be managed from the web dashboard when available.\n\n"
        "Reply SET2 for notification settings or 0 for main menu.",
    )


def _send_notification_settings(chat_id: str) -> None:
    send_telegram_text(
        chat_id,
        "*🔔 Notification Settings*\n\n"
        "Telegram notifications are active when this channel is linked.\n\n"
        "Use ACC2 if you want to unlink Telegram from your website account.\n"
        "More notification controls can be managed from the web dashboard when available.",
    )


def _send_privacy_settings(chat_id: str) -> None:
    send_telegram_text(
        chat_id,
        "*🔐 Privacy & Account Safety*\n\n"
        "• Keep your Telegram and website accounts secure.\n"
        "• Do not share payment links or OTP codes with strangers.\n"
        "• Use UNLINK if this Telegram account should no longer access your web workspace.\n\n"
        "Support: support@naijataxguides.com",
    )


def _handle_namespace_command(
    *,
    chat_id: str,
    account_id: str,
    tg_user_id: str,
    text_lower: str,
    linked: bool,
    has_subscription: bool,
) -> bool:
    cmd = text_lower.upper()

    if cmd == "ALL":
        _send_all_commands(chat_id, linked=linked)
        return True

    if cmd == "CR1":
        send_telegram_text(chat_id, format_balance_message(get_credit_balance(account_id)))
        return True

    if cmd == "CR2":
        _send_credit_package_menu(chat_id, account_id, has_subscription=has_subscription)
        return True

    if cmd == "CR3":
        _send_credit_activity(chat_id, account_id)
        return True

    if cmd == "CR4":
        _send_credit_rules(chat_id)
        return True

    if cmd == "PAY1":
        send_telegram_text(chat_id, format_subscription_message(account_id))
        return True

    if cmd == "PAY2":
        send_telegram_text(chat_id, get_plans_list_menu())
        return True

    if cmd == "PAY3":
        _send_upgrade_help(chat_id)
        return True

    if cmd == "PAY4":
        _send_payment_history(chat_id, account_id)
        return True

    if cmd == "PAY5":
        _send_renewal_help(chat_id)
        return True

    if cmd == "PAY6":
        _send_billing_support(chat_id)
        return True

    if cmd == "ACC1":
        _send_account_status(chat_id, account_id, tg_user_id, linked)
        return True

    if cmd == "ACC2":
        _send_link_help(chat_id, linked=linked)
        return True

    if cmd == "ACC3":
        _send_account_support(chat_id)
        return True

    if cmd == "SET1":
        _send_language_settings(chat_id)
        return True

    if cmd == "SET2":
        _send_notification_settings(chat_id)
        return True

    if cmd == "SET3":
        _send_privacy_settings(chat_id)
        return True

    return False


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
    has_subscription = bool(has_active_subscription(account_id))

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
            send_telegram_text(chat_id_str, "❌ Subscription cancelled. Reply PAY2 to see plans.")
            return jsonify({"ok": True})
        if "@" in email and "." in email:
            result = create_subscription_payment(account_id=account_id, plan=pending_plan, channel_type="telegram", provider_user_id=tg_user_id, email=email)
            send_telegram_text(chat_id_str, result.get("message") if result.get("ok") else f"❌ {result.get('message', 'Please try again.')}")
            user_states.pop(chat_id_str, None)
        else:
            send_telegram_text(chat_id_str, "❌ Invalid email. Send a valid email or CANCEL to abort.")
        return jsonify({"ok": True})

    # Direct top-up package commands work anytime and do not depend on in-memory worker state.
    if _select_credit_package_number(text_lower) is not None:
        if _handle_credit_package_selection(
            chat_id=chat_id_str,
            account_id=account_id,
            tg_user_id=tg_user_id,
            text_lower=text_lower,
            has_subscription=has_subscription,
        ):
            return jsonify({"ok": True})

    # Namespaced commands must override stale conversational state.
    if _handle_namespace_command(
        chat_id=chat_id_str,
        account_id=account_id,
        tg_user_id=tg_user_id,
        text_lower=text_lower,
        linked=linked,
        has_subscription=has_subscription,
    ):
        if text_lower.upper() != "CR2":
            user_states.pop(chat_id_str, None)
        return jsonify({"ok": True, "namespace_command": text_lower.upper()})

    if user_state.get("awaiting_credit_package"):
        if text_lower in ["0", "menu", "/menu"]:
            user_states.pop(chat_id_str, None)
            _send_main_menu(chat_id_str, linked=linked)
            return jsonify({"ok": True})
        send_telegram_text(chat_id_str, "Please reply T10, T50, T100, T500, or 0 to cancel. Other commands like PAY1, ACC1, and SET1 also work anytime.")
        return jsonify({"ok": True})

    if user_state.get("filing_type") and user_state.get("step"):
        _handle_continue_filing(chat_id_str, account_id, text)
        return jsonify({"ok": True})

    if _handle_tax_filing_command(chat_id_str, account_id, text):
        return jsonify({"ok": True})

    if MENU_NUMBER_RE.match(text):
        option = int(text)
        if option == 1:
            send_telegram_text(chat_id_str, "💬 Please type your Nigerian tax question.")
            return jsonify({"ok": True})
        if option == 2:
            send_telegram_text(chat_id_str, format_balance_message(get_credit_balance(account_id)))
            return jsonify({"ok": True})
        if option == 3:
            send_telegram_text(chat_id_str, format_subscription_message(account_id))
            return jsonify({"ok": True})
        if option == 4:
            send_telegram_text(chat_id_str, get_plans_list_menu())
            return jsonify({"ok": True})
        if option == 5:
            _send_link_help(chat_id_str, linked=linked)
            return jsonify({"ok": True})
        if option == 6:
            _send_credit_package_menu(chat_id_str, account_id, has_subscription=has_subscription)
            return jsonify({"ok": True})
        if option == 7:
            _send_tax_menu(chat_id_str)
            return jsonify({"ok": True})
        if option == 8:
            _send_help(chat_id_str, linked=linked)
            return jsonify({"ok": True})

    if re.match(r"^s[1-9]$", text_lower):
        plan_num = int(text_lower[1:])
        plan = validate_plan_number(plan_num)
        if not plan:
            send_telegram_text(chat_id_str, "❌ Invalid plan code. Reply PAY2 to see plans.")
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
        attempt = _try_consume_link_code(tg_user_id, text, display_name=display_name)
        if attempt.get("ok"):
            send_telegram_text(chat_id_str, "✅ *Telegram linked successfully!*\n\nYour Telegram account is now connected to the website workspace. Your plan and Usage Credits will now sync here.\n\nReply 0 to refresh your menu, or reply CR1 to check Usage Credits.")
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
