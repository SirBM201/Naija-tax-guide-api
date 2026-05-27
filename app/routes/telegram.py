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

TELEGRAM_ROUTE_VERSION = "2026-05-27-v34g-telegram-support-payment-guard-fix"

LINK_CODE_RE = re.compile(r"^[A-Z0-9]{8}$")
MENU_NUMBER_RE = re.compile(r"^[1-8]$")

# Temporary legacy state store retained from the existing Telegram route.
# A later Telegram batch should move this into a database-backed session table.
user_states: dict[str, dict[str, Any]] = {}

# Batch 27D1: lightweight per-worker throttle to avoid PATCHing channel_identities
# on every Telegram message. Database last_seen_at is still respected below.
TELEGRAM_IDENTITY_TOUCH_THROTTLE_SECONDS = int(os.getenv("TELEGRAM_IDENTITY_TOUCH_THROTTLE_SECONDS", "600"))
telegram_identity_touch_cache: dict[str, datetime] = {}


# ---------------------------------------------------------------------------
# Batch 27D: WhatsApp master command registry for Telegram
# ---------------------------------------------------------------------------
# WhatsApp is the source of truth:
# S1-S3 = Starter plans, P1-P3 = Professional plans, B1-B3 = Business plans.
# T10/T50/T100/T500 = Usage Credit add-ons.
# PAY1-PAY6 = billing/payment history, not subscription plan selection.
# Plain numbers 1-8 are reserved for the main menu only.

MASTER_PLAN_CODE_TO_NUMBER: dict[str, int] = {
    "S1": 1,
    "S2": 2,
    "S3": 3,
    "P1": 4,
    "P2": 5,
    "P3": 6,
    "B1": 7,
    "B2": 8,
    "B3": 9,
}

MASTER_TOPUP_CODE_TO_NUMBER: dict[str, int] = {
    "T10": 1,
    "T50": 2,
    "T100": 3,
    "T500": 4,
}

MASTER_COMMAND_RE = re.compile(
    r"^(ALL|ACC[1-3]|SET[1-3]|SUP[1-6]|CR[1-4]|PAY[1-6]|FT[1-8]|R[1-6]|"
    r"S[1-3]|P[1-3]|B[1-3]|T(?:10|50|100|500)|F[1-8]|C[1-8]|Q[1-5]|D[1-4]|H[1-2])\b",
    re.I,
)

INVALID_COMMAND_LIKE_RE = re.compile(
    r"^(?:ACC|SET|SUP|CR|PAY|FT|R|S|P|B|T|F|C|Q|D|H)\d+\b",
    re.I,
)



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



def _parse_dt(value: Any) -> Optional[datetime]:
    raw = _clean_text(value)
    if not raw:
        return None

    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _looks_like_bad_command(text: str) -> bool:
    """
    Stop obvious command typos from hitting the AI engine.
    This is intentionally conservative so normal questions like "what is PAYE?"
    or "cit filing deadline" are not blocked.
    """
    value = _clean_text(text).upper()
    if not value:
        return False

    first = value.split()[0]

    if first in {"ALL", "MENU", "START", "HELP", "BACK", "CANCEL", "UNLINK"}:
        return False

    if MASTER_COMMAND_RE.match(value):
        return False

    if INVALID_COMMAND_LIKE_RE.match(value):
        return True

    for prefix in ("ACC", "SET", "SUP", "PAY", "CR", "FT"):
        if first.startswith(prefix) and len(first) > len(prefix):
            return True

    if re.match(r"^T\d+\b", first):
        return True

    return False


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
            "command_namespace": "whatsapp_master_registry_stabilized_support_payment_guard",
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
    identity_id = _clean_text(identity.get("id"))
    if not identity_id:
        return

    now = datetime.now(timezone.utc)
    cache_key = identity_id

    last_local = telegram_identity_touch_cache.get(cache_key)
    if last_local and (now - last_local).total_seconds() < TELEGRAM_IDENTITY_TOUCH_THROTTLE_SECONDS:
        return

    last_seen = _parse_dt(identity.get("last_seen_at") or identity.get("updated_at"))
    metadata = identity.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}

    needs_metadata_update = bool(display_name and not metadata.get("display_name"))

    if (
        last_seen
        and (now - last_seen).total_seconds() < TELEGRAM_IDENTITY_TOUCH_THROTTLE_SECONDS
        and not needs_metadata_update
    ):
        telegram_identity_touch_cache[cache_key] = now
        return

    payload: dict[str, Any] = {"last_seen_at": _utc_now_iso()}

    if needs_metadata_update:
        metadata["display_name"] = display_name
        payload["metadata"] = metadata

    try:
        supabase.table("channel_identities").update(payload).eq("id", identity_id).execute()
        telegram_identity_touch_cache[cache_key] = now
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
    option_5 = "5️⃣ Unlink website account 🔓" if linked else "5️⃣ Link website account 🔗"
    menu = (
        "🇳🇬 *Naija Tax Guide*\n\n"
        "Reply with:\n"
        "1️⃣ Ask a tax question\n"
        "2️⃣ Check Usage Credits 💎\n"
        "3️⃣ Check current plan 📌\n"
        "4️⃣ View subscription plans 🛒\n"
        f"{option_5}\n"
        "6️⃣ Buy Usage Credit add-ons 💳\n"
        "7️⃣ Tax tools, filing & quiz 🧰\n"
        "8️⃣ Help / Menu ℹ️\n\n"
        "Quick commands:\n"
        "H1 - Recent tax history 🕘\n"
        "H2 - Last tax answer 📌\n"
        "SUP1 - Create support ticket 🛟\n"
        "SUP2 - View support tickets 🎫\n"
        "PAY1 - Billing summary 💳\n"
        "PAY2 - Payment history 🧾\n"
        "FT1 - Filing assistance 🗂️\n"
        "FT7 - Request human filing help 🧑‍💼\n"
        "R1 - My referral code/link 🤝\n"
        "R4 - Referral statistics 📊\n"
        "ACC1 - My account profile 👤\n"
        "SET1 - Notification settings ⚙️\n"
        "ALL - Full command list 📋\n"
        "0 or MENU - Main menu 🏠\n"
        "* or BACK - Go back ↩️\n"
        "CANCEL - Cancel current flow ❌\n\n"
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
        "• View/upgrade plans: reply 4 then choose S1, P1, or B1\n"
        f"• {option_5}: reply 5 or ACC2\n"
        "• Buy Usage Credit add-ons: reply 6 then choose T10, T50, T100, or T500\n"
        "• Recent history: H1 / H2\n"
        "• Support: SUP1-SUP6\n"
        "• Referrals: R1-R6\n"
        "• Show all commands: ALL\n"
        "• Show menu: 0 or MENU\n\n"
        "Need help? Email support@naijataxguides.com"
    )
    send_telegram_text(chat_id, help_msg)


def _send_welcome(chat_id: str, *, linked: bool = False) -> None:
    send_telegram_text(chat_id, "*Welcome to Naija Tax Guide!* ✅\n\nI'm your AI tax assistant for Nigerian taxes.")
    _send_main_menu(chat_id, linked=linked)




# ---------------------------------------------------------------------------
# Batch 27D WhatsApp master registry helpers
# ---------------------------------------------------------------------------

def _clip_text(value: Any, limit: int = 3900) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: max(0, limit - 3)].rstrip() + "..."


def _master_plans_menu() -> str:
    return (
        "📌 *Subscription Plans*\n\n"
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
        "Do not use plain numbers here. Plain numbers are for the main menu only."
    )


def _topup_menu_text() -> str:
    return (
        "💎 *Usage Credit Add-ons*\n\n"
        "T10 - 10 credits - ₦500\n"
        "T50 - 50 credits - ₦2,000\n"
        "T100 - 100 credits - ₦3,500\n"
        "T500 - 500 credits - ₦15,000\n\n"
        "Reply with T10, T50, T100, or T500.\n"
        "Add-ons are available only to active paid subscribers."
    )


def _invalid_command_text(value: str = "") -> str:
    shown = f"\n\nReceived: {value}" if value else ""
    return (
        "⚠️ That menu code is not available, so no AI credit was used."
        f"{shown}\n\n"
        "Useful commands:\n"
        "0 - Main menu\n"
        "ALL - Full command list\n"
        "S1/P1/B1 - Subscription plans\n"
        "T10/T50/T100/T500 - Credit add-ons\n"
        "PAY1 - Billing summary\n"
        "CR1 - Credit balance\n"
        "H1 - Recent tax history"
    )


def _plan_number_from_master_code(text_lower: str) -> Optional[int]:
    return MASTER_PLAN_CODE_TO_NUMBER.get(text_lower.upper())


def _topup_number_from_master_code(text_lower: str) -> Optional[int]:
    return MASTER_TOPUP_CODE_TO_NUMBER.get(text_lower.upper())


def _row_recent_enough(row: dict[str, Any], seconds: int = 900) -> bool:
    dt = _parse_dt(row.get("created_at") or row.get("updated_at"))
    if not dt:
        return False
    return (datetime.now(timezone.utc) - dt).total_seconds() <= seconds


def _checkout_url_from_row(row: dict[str, Any]) -> str:
    for key in ("authorization_url", "checkout_url", "payment_url", "url", "provider_url", "payment_link"):
        value = _clean_text(row.get(key))
        if value.startswith("http"):
            return value
    return ""


def _row_is_open_checkout(row: dict[str, Any]) -> bool:
    status = _clean_text(row.get("status") or row.get("payment_status") or row.get("transaction_status")).lower()
    if status in {"success", "successful", "paid", "completed", "verified"}:
        return False
    return True


def _recent_checkout_reuse_message(account_id: str, *, plan_code: str = "", package_code: str = "") -> Optional[str]:
    """
    Batch 27D2:
    Stronger duplicate checkout guard.

    D1 only reused a checkout when a URL was found in the existing row.
    Some paystack_transactions rows do not expose/store the authorization URL,
    so repeated S1/T10 could still create multiple pending rows.

    This guard now:
      - reuses the URL when available;
      - otherwise blocks a very recent matching/open checkout and tells the user
        to use the last link already shown in Telegram.
    """
    rows = _rows_for_account("paystack_transactions", account_id, limit=20)

    requested_label = (plan_code or package_code or "checkout").upper()
    requested_terms = [x.lower() for x in (plan_code, package_code) if x]

    recent_open_fallback: Optional[dict[str, Any]] = None

    for row in rows:
        if not _row_is_open_checkout(row) or not _row_recent_enough(row, seconds=900):
            continue

        row_text = " ".join(
            _clean_text(row.get(k)).lower()
            for k in (
                "plan_code", "package_code", "product_code", "purpose", "type",
                "metadata", "description", "reference", "payment_reference",
                "provider_reference", "gateway_reference", "plan", "package",
                "channel_type", "source"
            )
        )

        is_match = False

        if requested_terms:
            is_match = any(term and term in row_text for term in requested_terms)

        # Very recent open checkout fallback.
        # This prevents double-click / repeated-command duplicates even when the
        # DB row does not store plan_code/package_code in a searchable field.
        if not is_match and _row_recent_enough(row, seconds=120):
            if recent_open_fallback is None:
                recent_open_fallback = row
            continue

        if not is_match:
            continue

        url = _checkout_url_from_row(row)
        ref = (
            row.get("reference")
            or row.get("payment_reference")
            or row.get("provider_reference")
            or row.get("gateway_reference")
            or "not shown"
        )

        if url:
            return (
                "🧾 *Recent Checkout Found*\n\n"
                f"I found a recent pending checkout for {requested_label}. To avoid duplicate payment records, use this existing checkout link:\n\n"
                f"{url}\n\n"
                f"Reference: {ref}\n\n"
                "If the link has expired, wait a few minutes or contact support."
            )

        return (
            "🧾 *Recent Pending Checkout Found*\n\n"
            f"You already have a recent pending checkout for {requested_label}.\n\n"
            "Please use the last payment link already shown above in this chat. "
            "To avoid duplicate payment records, I will not create another checkout immediately.\n\n"
            f"Reference: {ref}\n\n"
            "If you cannot find the link, wait about 15 minutes and try again, or contact support with SUP6."
        )

    if recent_open_fallback:
        ref = (
            recent_open_fallback.get("reference")
            or recent_open_fallback.get("payment_reference")
            or recent_open_fallback.get("provider_reference")
            or recent_open_fallback.get("gateway_reference")
            or "not shown"
        )
        url = _checkout_url_from_row(recent_open_fallback)

        if url:
            return (
                "🧾 *Recent Checkout Found*\n\n"
                "I found a very recent pending checkout on your account. "
                "To avoid duplicate payment records, complete this checkout first:\n\n"
                f"{url}\n\n"
                f"Reference: {ref}"
            )

        return (
            "🧾 *Recent Pending Checkout Found*\n\n"
            "I found a very recent pending checkout on your account. "
            "Please use the last payment link already shown above before creating another one.\n\n"
            f"Reference: {ref}\n\n"
            "If you cannot find the link, wait about 15 minutes and try again."
        )

    return None


def _handle_plan_code_selection(
    *,
    chat_id: str,
    account_id: str,
    tg_user_id: str,
    text_lower: str,
) -> bool:
    plan_num = _plan_number_from_master_code(text_lower)
    if plan_num is None:
        return False

    plan = validate_plan_number(plan_num)
    if not plan:
        send_telegram_text(chat_id, "❌ Invalid plan code. Reply 4 to view plans again.")
        return True

    plan_code = _clean_text(plan.get("plan_code") or plan.get("code") or plan.get("slug") or "")
    reuse_msg = _recent_checkout_reuse_message(account_id, plan_code=plan_code) if plan_code else None
    if reuse_msg:
        send_telegram_text(chat_id, reuse_msg)
        return True

    user_email = get_user_email(account_id)
    if user_email:
        result = create_subscription_payment(
            account_id=account_id,
            plan=plan,
            channel_type="telegram",
            provider_user_id=tg_user_id,
            email=user_email,
        )
        send_telegram_text(
            chat_id,
            result.get("message") if result.get("ok") else f"❌ {result.get('message', 'Please try again.')}",
        )
    else:
        user_states[chat_id] = {"awaiting_email": True, "pending_plan": plan}
        send_telegram_text(chat_id, request_email_message())

    return True


def _rows_for_account(table_name: str, account_id: str, limit: int = 5, select_cols: str = "*") -> list[dict[str, Any]]:
    try:
        resp = (
            supabase.table(table_name)
            .select(select_cols)
            .eq("account_id", account_id)
            .order("created_at", desc=True)
            .limit(max(1, min(limit, 20)))
            .execute()
        )
        return _rows(resp)
    except Exception:
        return []


def _history_date_label(value: Any) -> str:
    raw = _clean_text(value)
    if not raw:
        return "date not shown"
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return raw[:19]


def _history_excerpt(value: Any, limit: int = 220) -> str:
    text = re.sub(r"\s+", " ", _clean_text(value))
    if not text:
        return "Not shown"
    return text if len(text) <= limit else text[: max(0, limit - 3)].rstrip() + "..."


def _history_rows(account_id: str, limit: int = 5) -> list[dict[str, Any]]:
    return _rows_for_account(
        "qa_history",
        account_id,
        limit=limit,
        select_cols="id,question,answer,source,provider,channel,created_at,credits_consumed,usage_charged",
    )


def _send_recent_history(chat_id: str, account_id: str) -> None:
    rows = _history_rows(account_id, limit=5)

    if not rows:
        send_telegram_text(
            chat_id,
            "🕘 *Recent Tax History*\n\n"
            "No tax history found yet.\n\n"
            "Ask a tax question here or on the website, then reply H1 again.",
        )
        return

    lines = ["🕘 *Recent Tax History*", ""]
    for index, row in enumerate(rows, start=1):
        question = _history_excerpt(row.get("question"), 110)
        source = _clean_text(row.get("channel") or row.get("provider") or row.get("source") or "app")
        created = _history_date_label(row.get("created_at"))
        try:
            credits = int(row.get("credits_consumed") or 0)
        except Exception:
            credits = 0
        credit_text = f" | credits: {credits}" if credits else ""
        lines.append(f"{index}. {question}")
        lines.append(f"   {created} | {source}{credit_text}")

    lines.extend(["", "Reply H2 to view your last tax answer, or 0 for main menu."])
    send_telegram_text(chat_id, _clip_text("\n".join(lines)))


def _send_last_answer(chat_id: str, account_id: str) -> None:
    rows = _history_rows(account_id, limit=1)

    if not rows:
        send_telegram_text(
            chat_id,
            "📌 *Last Tax Answer*\n\n"
            "No saved tax answer found yet.\n\n"
            "Ask a tax question first, then reply H2 again.",
        )
        return

    row = rows[0]
    question = _history_excerpt(row.get("question"), 500)
    answer = _history_excerpt(row.get("answer"), 2500)
    created = _history_date_label(row.get("created_at"))
    source = _clean_text(row.get("channel") or row.get("provider") or row.get("source") or "app")
    try:
        credits = int(row.get("credits_consumed") or 0)
    except Exception:
        credits = 0

    body = (
        "📌 *Last Tax Answer*\n\n"
        f"Date: {created}\n"
        f"Source: {source}\n"
        f"Credits used: {credits}\n\n"
        f"Question:\n{question}\n\n"
        f"Answer:\n{answer}\n\n"
        "Reply H1 for recent history or 0 for main menu."
    )
    send_telegram_text(chat_id, _clip_text(body))


def _log_telegram_history(*, account_id: str, question: str, answer: str, result: dict[str, Any]) -> None:
    try:
        meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
        credits_consumed = int(meta.get("credits_consumed") or 0)
        from_cache = bool(result.get("source") == "database" or result.get("mode") == "direct_cache")
        payload = {
            "account_id": account_id or None,
            "question": _clip_text(question, 5000),
            "answer": _clip_text(answer, 20000),
            "lang": "en",
            "source": "telegram",
            "from_cache": from_cache,
            "credits_consumed": credits_consumed,
            "usage_charged": bool(credits_consumed > 0 or meta.get("usage_charged") is True),
            "channel": "telegram",
            "created_at": _utc_now_iso(),
            "updated_at": _utc_now_iso(),
        }
        supabase.table("qa_history").insert(payload).execute()
    except Exception:
        # History must not break the Telegram answer flow.
        pass


def _subscription_row(account_id: str) -> Optional[dict[str, Any]]:
    try:
        resp = supabase.table("user_subscriptions").select("*").eq("account_id", account_id).order("created_at", desc=True).limit(1).execute()
        return _first(resp)
    except Exception:
        return None


def _billing_summary_text(account_id: str) -> str:
    sub = _subscription_row(account_id)
    balance = get_credit_balance(account_id)
    bal_value = balance.get("balance", 0) if isinstance(balance, dict) else 0

    if not sub:
        return (
            "💳 *Billing Summary*\n\n"
            "Current plan: Free Forever\n"
            f"Usage Credits: {bal_value}\n"
            "Status: Free access\n\n"
            "Reply 4 to view subscription plans, PAY2 for payment history, or 0 for main menu."
        )

    plan_name = _clean_text(sub.get("plan_name") or sub.get("plan_code") or "Current plan")
    status = _clean_text(sub.get("status") or "active")
    expiry = _clean_text(sub.get("expires_at") or sub.get("current_period_end") or sub.get("valid_until") or "")
    ref = _clean_text(sub.get("provider_ref") or sub.get("paystack_ref") or sub.get("payment_reference") or sub.get("reference") or "")

    body = (
        "💳 *Billing Summary*\n\n"
        f"Plan: {plan_name}\n"
        f"Status: {status}\n"
        f"Usage Credits: {bal_value}\n"
    )
    if expiry:
        body += f"Renewal/expiry: {expiry[:10]}\n"
    if ref:
        body += f"Reference: {ref}\n"
    body += "\nReply PAY2 for payment history, PAY6 for renewal/expiry, or 0 for main menu."
    return body


def _payment_rows(account_id: str, limit: int = 5) -> list[dict[str, Any]]:
    for table_name in ("paystack_transactions", "payment_transactions", "billing_transactions"):
        rows = _rows_for_account(table_name, account_id, limit=limit)
        if rows:
            return rows
    return []


def _payment_row_line(row: dict[str, Any], index: int) -> str:
    plan = row.get("plan_code") or row.get("plan") or row.get("product_code") or row.get("purpose") or "Payment"
    status = row.get("status") or row.get("payment_status") or row.get("event") or "status not shown"
    amount = row.get("amount") or row.get("amount_naira") or row.get("price") or row.get("paid_amount") or row.get("amount_kobo")
    reference = row.get("reference") or row.get("payment_reference") or row.get("provider_reference") or ""
    created_at = row.get("created_at") or row.get("paid_at") or row.get("updated_at")

    line = f"{index}. {plan}\n"
    if amount is not None:
        line += f"   Amount: {_money(amount)}\n"
    line += f"   Status: {status}\n"
    if reference:
        line += f"   Ref: {reference}\n"
    line += f"   Date: {_date_short(created_at)}"
    return line


def _send_payment_history_master(chat_id: str, account_id: str) -> None:
    rows = _payment_rows(account_id, limit=5)

    if not rows:
        send_telegram_text(
            chat_id,
            "🧾 *Payment History*\n\n"
            "No payment history found for this account yet.\n\n"
            "Reply 4 to view plans or PAY1 for billing summary.",
        )
        return

    lines = ["🧾 *Recent Payment History*", ""]
    for idx, row in enumerate(rows, 1):
        lines.append(_payment_row_line(row, idx))
        lines.append("")

    lines.append("Reply PAY1 for billing summary, PAY3 for latest payment, or 0 for main menu.")
    send_telegram_text(chat_id, _clip_text("\n".join(lines)))


def _send_latest_payment(chat_id: str, account_id: str) -> None:
    rows = _payment_rows(account_id, limit=1)
    if not rows:
        send_telegram_text(chat_id, "🧾 *Latest Payment Status*\n\nNo payment record found yet.\n\nReply PAY2 for payment history or 4 to view plans.")
        return

    send_telegram_text(chat_id, "🧾 *Latest Payment Status*\n\n" + _payment_row_line(rows[0], 1) + "\n\nReply PAY2 for payment history or 0 for main menu.")


def _send_verify_payment(chat_id: str, account_id: str, text_raw: str) -> None:
    parts = _clean_text(text_raw).split(maxsplit=1)
    reference = parts[1].strip() if len(parts) > 1 else ""

    if not reference:
        send_telegram_text(
            chat_id,
            "🔎 *Verify Payment Reference*\n\n"
            "Send PAY4 followed by your payment reference.\n\n"
            "Example:\n"
            "PAY4 NTG-WA-ABC123",
        )
        return

    # Batch 27D2:
    # Logs confirmed paystack_transactions.reference exists, while
    # payment_reference/provider_reference cause 400 on the current schema.
    # Keep PAY4 stable by querying only the confirmed reference column.
    try:
        resp = (
            supabase.table("paystack_transactions")
            .select("*")
            .eq("account_id", account_id)
            .eq("reference", reference)
            .limit(1)
            .execute()
        )
        row = _first(resp)
    except Exception:
        logging.exception("Telegram PAY4 reference lookup failed")
        row = None

    if not row:
        send_telegram_text(
            chat_id,
            f"🔎 *Payment Reference Check*\n\nNo payment record found for:\n{reference}\n\nIf payment was recent, wait a few minutes or contact support with SUP6.",
        )
        return

    send_telegram_text(chat_id, "🔎 *Payment Reference Check*\n\n" + _payment_row_line(row, 1))


def _send_pending_change(chat_id: str, account_id: str) -> None:
    sub = _subscription_row(account_id)
    if not sub:
        send_telegram_text(chat_id, "📌 *Pending Plan Change*\n\nNo active subscription found.\n\nReply 4 to view subscription plans.")
        return

    pending = (
        sub.get("pending_plan_code")
        or sub.get("pending_change")
        or sub.get("scheduled_plan_code")
        or sub.get("next_plan_code")
    )
    if pending:
        send_telegram_text(chat_id, f"📌 *Pending Plan Change*\n\nPending change: {pending}\n\nReply PAY1 for billing summary or PAY6 for renewal/expiry.")
    else:
        send_telegram_text(chat_id, "📌 *Pending Plan Change*\n\nNo pending plan change found.\n\nReply PAY1 for billing summary or 0 for main menu.")


def _send_renewal_expiry(chat_id: str, account_id: str) -> None:
    sub = _subscription_row(account_id)
    if not sub:
        send_telegram_text(chat_id, "📅 *Renewal / Expiry Date*\n\nNo active paid subscription found.\n\nReply 4 to view subscription plans.")
        return

    expiry = _clean_text(sub.get("expires_at") or sub.get("current_period_end") or sub.get("valid_until") or "")
    plan_name = _clean_text(sub.get("plan_name") or sub.get("plan_code") or "Current plan")
    send_telegram_text(
        chat_id,
        "📅 *Renewal / Expiry Date*\n\n"
        f"Plan: {plan_name}\n"
        f"Renewal/expiry: {expiry[:10] if expiry else 'Not shown'}\n\n"
        "Reply PAY1 for billing summary or PAY2 for payment history.",
    )


def _send_credit_rows(chat_id: str, account_id: str, *, mode: str) -> None:
    rows = _rows_for_account("credit_usage_logs", account_id, limit=8)

    if mode == "ai":
        rows = [r for r in rows if "ai" in _clean_text(r.get("action_code") or r.get("description")).lower() or int(r.get("credits_delta") or 0) < 0]
        title = "📉 *AI Credit Deductions*"
    elif mode == "additions":
        rows = [r for r in rows if int(r.get("credits_delta") or 0) > 0 or "top" in _clean_text(r.get("action_code") or r.get("description")).lower()]
        title = "➕ *Credit Additions / Top-ups*"
    else:
        title = "💎 *Recent Credit Activity*"

    if not rows:
        balance = get_credit_balance(account_id)
        bal_value = balance.get("balance", 0) if isinstance(balance, dict) else 0
        send_telegram_text(chat_id, f"{title}\n\nNo matching credit activity found yet.\n\nCurrent balance: {bal_value}\n\nReply CR1 for balance or 0 for main menu.")
        return

    lines = [title, ""]
    for idx, row in enumerate(rows[:5], 1):
        desc = row.get("description") or row.get("action_code") or "Credit activity"
        delta = row.get("credits_delta") or row.get("amount") or row.get("credits") or ""
        created = row.get("created_at") or row.get("updated_at")
        lines.append(f"{idx}. {desc}")
        if delta != "":
            lines.append(f"   Credits: {delta}")
        lines.append(f"   Date: {_date_short(created)}")
        lines.append("")

    lines.append("Reply CR1 for balance, T10/T50/T100/T500 for top-up, or 0 for menu.")
    send_telegram_text(chat_id, _clip_text("\n".join(lines)))


def _send_support_menu(chat_id: str) -> None:
    send_telegram_text(
        chat_id,
        "🛟 *Support Centre*\n\n"
        "SUP1 - Create support ticket\n"
        "SUP2 - View my support tickets\n"
        "SUP3 - View latest ticket\n"
        "SUP4 - Reply to latest/open ticket\n"
        "SUP5 - Close latest/open ticket\n"
        "SUP6 - Contact support email\n\n"
        "Quick examples:\n"
        "SUP1 I paid but my plan has not updated. Reference NTG-...\n"
        "SUP4 Please note that my Paystack reference is NTG-...\n"
        "SUP5\n\n"
        "Reply 0 for main menu.",
    )


def _send_support_email(chat_id: str) -> None:
    send_telegram_text(
        chat_id,
        "📧 *Contact Support*\n\n"
        "Email: support@naijataxguides.com\n\n"
        "For faster help, include your Telegram ID, registered email/phone, payment reference if any, and a short description of the issue.\n\n"
        "You can also reply SUP1 followed by your issue to create a support ticket.",
    )


def _create_support_ticket_from_text(chat_id: str, account_id: str, text_raw: str) -> None:
    details = _clean_text(text_raw)
    details = re.sub(r"^SUP1\b", "", details, flags=re.I).strip()

    if not details:
        send_telegram_text(
            chat_id,
            "🛟 *Create Support Ticket*\n\n"
            "Send SUP1 followed by your issue.\n\n"
            "Example:\n"
            "SUP1 I paid but my plan has not updated. Reference NTG-...",
        )
        return

    ticket_id = f"NTG-TG-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    now = _utc_now_iso()

    # Batch 27D2:
    # Live DB error confirmed support_tickets.ticket_id is NOT NULL.
    # Use ticket_id in every payload attempt and keep the first payload close
    # to the actual row shape shown by Supabase error details.
    payload_attempts = [
        {
            "ticket_id": ticket_id,
            "account_id": account_id,
            "category": "general",
            "priority": "normal",
            "subject": details[:120],
            "message": details,
            "status": "open",
            "channel": "telegram",
            "source": "telegram",
            "metadata": {"created_from": "telegram", "command": "SUP1"},
            "created_at": now,
            "updated_at": now,
        },
        {
            "ticket_id": ticket_id,
            "account_id": account_id,
            "subject": details[:120],
            "message": details,
            "status": "open",
            "channel": "telegram",
            "source": "telegram",
            "created_at": now,
            "updated_at": now,
        },
        {
            "ticket_id": ticket_id,
            "account_id": account_id,
            "subject": details[:120],
            "message": details,
            "status": "open",
            "created_at": now,
            "updated_at": now,
        },
        {
            "ticket_id": ticket_id,
            "account_id": account_id,
            "message": details,
            "status": "open",
            "created_at": now,
            "updated_at": now,
        },
        {
            "ticket_id": ticket_id,
            "account_id": account_id,
            "message": details,
            "status": "open",
            "created_at": now,
        },
    ]

    saved_row = None
    last_error = None

    for payload in payload_attempts:
        ok, resp, err = _safe_exec(supabase.table("support_tickets").insert(payload))
        if ok:
            saved_row = _first(resp) or payload
            break
        last_error = err

    if saved_row:
        ref = saved_row.get("ticket_id") or saved_row.get("id") or ticket_id
        send_telegram_text(
            chat_id,
            f"✅ *Support Ticket Created*\n\nTicket: {ref}\n\nOur support team will review it.\n\nReply SUP2 to view tickets or 0 for main menu.",
        )
    else:
        logging.warning("Telegram support ticket insert failed: %s", last_error)
        send_telegram_text(
            chat_id,
            "⚠️ I could not save the support ticket automatically.\n\n"
            "Please email support@naijataxguides.com and include this reference:\n"
            f"{ticket_id}\n\n"
            "Your message:\n"
            f"{_clip_text(details, 600)}",
        )


def _send_support_tickets(chat_id: str, account_id: str, latest_only: bool = False) -> None:
    rows = _rows_for_account("support_tickets", account_id, limit=1 if latest_only else 5)

    if not rows:
        send_telegram_text(chat_id, "🎫 *Support Tickets*\n\nNo support ticket found yet.\n\nReply SUP1 followed by your issue to create one.")
        return

    title = "🎫 *Latest Support Ticket*" if latest_only else "🎫 *My Support Tickets*"
    lines = [title, ""]
    for idx, row in enumerate(rows, 1):
        ref = row.get("ticket_id") or row.get("id") or "No reference"
        status = row.get("status") or "open"
        subject = row.get("subject") or row.get("title") or row.get("message") or row.get("description") or "Support ticket"
        created = row.get("created_at")
        lines.append(f"{idx}. {ref}")
        lines.append(f"   Status: {status}")
        lines.append(f"   Subject: {_history_excerpt(subject, 120)}")
        lines.append(f"   Date: {_date_short(created)}")
        lines.append("")

    lines.append("Reply SUP4 with your message to add a note, SUP5 to close latest/open ticket, or 0 for menu.")
    send_telegram_text(chat_id, _clip_text("\n".join(lines)))


def _send_referral_menu(chat_id: str, account_id: str, action: str) -> None:
    fallback_code = f"NTG-{account_id.replace('-', '')[:8].upper()}" if account_id else "NTG-USER"
    base = os.getenv("FRONTEND_BASE_URL") or os.getenv("APP_BASE_URL") or "https://www.naijataxguides.com"
    link = f"{base.rstrip()}/?ref={fallback_code}"

    if action == "r1":
        send_telegram_text(chat_id, f"🤝 *My Referral Code*\n\nCode: {fallback_code}\n\nReply R2 for your referral link or R3 for a share message.")
    elif action == "r2":
        send_telegram_text(chat_id, f"🔗 *My Referral Link*\n\n{link}\n\nReply R3 for a ready-to-share invitation.")
    elif action == "r3":
        send_telegram_text(chat_id, f"📣 *Referral Invitation*\n\nUse Naija Tax Guide for Nigerian tax answers, calculators, reminders, and filing support.\n\nJoin here:\n{link}")
    elif action == "r4":
        send_telegram_text(chat_id, "📊 *Referral Statistics*\n\nReferral statistics are available on the website dashboard.\n\nReply R1 for your code or R2 for your link.")
    elif action == "r5":
        send_telegram_text(chat_id, "💰 *Referral Rewards*\n\nReferral rewards are tracked on your web dashboard.\n\nReply R6 for payout status.")
    elif action == "r6":
        send_telegram_text(chat_id, "🏦 *Payout Status*\n\nReferral payout status is available on your web dashboard.\n\nContact support if a payout is delayed.")
    else:
        send_telegram_text(
            chat_id,
            "🤝 *Referral Centre*\n\n"
            "R1 - My referral code\n"
            "R2 - My referral link\n"
            "R3 - Share referral invitation\n"
            "R4 - Referral statistics\n"
            "R5 - Referral rewards\n"
            "R6 - Payout status",
        )


def _send_filing_assistance(chat_id: str, action: str) -> None:
    if action == "ft1":
        _send_tax_menu(chat_id)
        return

    messages = {
        "ft2": "👥 *PAYE Filing Help*\n\nUse this for employee salary tax guidance, PAYE calculations, and payroll filing preparation.\n\nYou can also use C1 for PAYE calculator.",
        "ft3": "🧾 *VAT Filing Help*\n\nUse this for VAT registration, output VAT/input VAT guidance, and VAT filing preparation.\n\nYou can also use C3 for VAT calculator.",
        "ft4": "🏢 *CIT Filing Help*\n\nUse this for Company Income Tax filing preparation, records, and tax computation guidance.\n\nYou can also use C2 for CIT calculator.",
        "ft5": "💼 *WHT Filing Help*\n\nUse this for withholding tax deduction/remittance guidance.\n\nYou can also use C4 for WHT calculator.",
        "ft6": "✅ *Document Checklist*\n\nCommon tax filing documents include financial statements, invoices, receipts, bank statements, payroll records, WHT credit notes, VAT records, and prior filings.",
        "ft7": "🧑‍💼 *Human-Assisted Filing Request*\n\nSend SUP1 followed by your filing request and contact details.\n\nExample:\nSUP1 I need help filing VAT for April 2026.",
        "ft8": "📌 *Latest Filing Request*\n\nUse the website filing page for detailed request tracking. Reply 7 for the Telegram filing menu.",
    }
    send_telegram_text(chat_id, messages.get(action, "🗂️ *Filing Assistance*\n\nReply FT1 to open the filing assistance menu."))


def _send_account_profile(chat_id: str, account_id: str, tg_user_id: str, linked: bool, action: str) -> None:
    if action == "acc1":
        _send_account_status(chat_id, account_id, tg_user_id, linked)
    elif action == "acc2":
        _send_link_help(chat_id, linked=linked)
    else:
        _send_account_support(chat_id)


def _send_settings_master(chat_id: str, action: str) -> None:
    if action == "set1":
        send_telegram_text(
            chat_id,
            "⚙️ *Notification Settings*\n\n"
            "Telegram notifications are active when your Telegram channel is linked.\n\n"
            "For sensitive notification changes, use the website dashboard.",
        )
    elif action == "set2":
        send_telegram_text(
            chat_id,
            "🕘 *Reminder Timezone / Defaults*\n\n"
            "Default reminder timezone is managed from the website dashboard.\n\n"
            "Use D1-D4 for deadline/reminder commands where available.",
        )
    else:
        send_telegram_text(
            chat_id,
            "🔐 *Privacy / Data Options*\n\n"
            "Keep your Telegram and website accounts secure. Do not share OTPs, payment links, or link codes with strangers.\n\n"
            "Use UNLINK if this Telegram account should no longer access your web workspace.",
        )


def _handle_master_command(
    *,
    chat_id: str,
    account_id: str,
    tg_user_id: str,
    text_raw: str,
    linked: bool,
    has_subscription: bool,
) -> bool:
    text_clean = _clean_text(text_raw)
    text_lower = text_clean.lower()
    match = MASTER_COMMAND_RE.match(text_clean)
    if not match:
        return False

    cmd = match.group(1).upper()

    if cmd == "ALL":
        _send_all_commands(chat_id, linked=linked)
        return True

    if cmd in MASTER_PLAN_CODE_TO_NUMBER:
        return _handle_plan_code_selection(
            chat_id=chat_id,
            account_id=account_id,
            tg_user_id=tg_user_id,
            text_lower=cmd.lower(),
        )

    if cmd in MASTER_TOPUP_CODE_TO_NUMBER:
        return _handle_credit_package_selection(
            chat_id=chat_id,
            account_id=account_id,
            tg_user_id=tg_user_id,
            text_lower=cmd.lower(),
            has_subscription=has_subscription,
        )

    if cmd in {"H1", "H2"}:
        if cmd == "H1":
            _send_recent_history(chat_id, account_id)
        else:
            _send_last_answer(chat_id, account_id)
        return True

    if cmd.startswith("SUP"):
        if cmd == "SUP1":
            _create_support_ticket_from_text(chat_id, account_id, text_raw)
        elif cmd == "SUP2":
            _send_support_tickets(chat_id, account_id, latest_only=False)
        elif cmd == "SUP3":
            _send_support_tickets(chat_id, account_id, latest_only=True)
        elif cmd == "SUP4":
            send_telegram_text(chat_id, "📝 *Reply to Ticket*\n\nSend SUP4 followed by your reply. Ticket reply threading will be fully expanded in a later support-specific batch.")
        elif cmd == "SUP5":
            send_telegram_text(chat_id, "✅ *Close Ticket*\n\nTicket closing from Telegram will be fully expanded in a later support-specific batch. For now, email support@naijataxguides.com if urgent.")
        elif cmd == "SUP6":
            _send_support_email(chat_id)
        return True

    if cmd.startswith("R"):
        _send_referral_menu(chat_id, account_id, cmd.lower())
        return True

    if cmd.startswith("FT"):
        _send_filing_assistance(chat_id, cmd.lower())
        return True

    if cmd.startswith("ACC"):
        _send_account_profile(chat_id, account_id, tg_user_id, linked, cmd.lower())
        return True

    if cmd.startswith("SET"):
        _send_settings_master(chat_id, cmd.lower())
        return True

    if cmd == "CR1":
        send_telegram_text(chat_id, format_balance_message(get_credit_balance(account_id)))
        return True
    if cmd == "CR2":
        _send_credit_rows(chat_id, account_id, mode="recent")
        return True
    if cmd == "CR3":
        _send_credit_rows(chat_id, account_id, mode="ai")
        return True
    if cmd == "CR4":
        _send_credit_rows(chat_id, account_id, mode="additions")
        return True

    if cmd == "PAY1":
        send_telegram_text(chat_id, _billing_summary_text(account_id))
        return True
    if cmd == "PAY2":
        _send_payment_history_master(chat_id, account_id)
        return True
    if cmd == "PAY3":
        _send_latest_payment(chat_id, account_id)
        return True
    if cmd == "PAY4":
        _send_verify_payment(chat_id, account_id, text_raw)
        return True
    if cmd == "PAY5":
        _send_pending_change(chat_id, account_id)
        return True
    if cmd == "PAY6":
        _send_renewal_expiry(chat_id, account_id)
        return True

    # These modules will be expanded in later platform-parity batches.
    if cmd.startswith("F"):
        if cmd == "F1":
            send_telegram_text(chat_id, "🧮 Calculator menu is available on WhatsApp and web. Telegram calculator parity will be handled in a later batch.\n\nUse C1-C5 if enabled, or use the website Calculator page.")
        else:
            _send_filing_assistance(chat_id, "ft1")
        return True

    if cmd.startswith("C"):
        send_telegram_text(chat_id, "🧮 Calculator command received. Full Telegram calculator parity will be handled in a later batch. Use the website Calculator page for now.")
        return True

    if cmd.startswith("Q"):
        send_telegram_text(chat_id, "🧠 Quiz command received. Full Telegram quiz parity will be handled in a later batch. WhatsApp quiz remains the current live quiz channel.")
        return True

    if cmd.startswith("D"):
        send_telegram_text(chat_id, "📅 Deadline command received. Full Telegram deadline/reminder parity will be handled in a later batch. Use the website Deadlines page for now.")
        return True

    return False


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
    msg = (
        "📋 *Naija Tax Guide Command List*\n\n"
        "Main menu:\n"
        "1 - Ask a tax question\n"
        "2 - Check Usage Credits\n"
        "3 - Check current plan\n"
        "4 - View subscription plans\n"
        "5 - Link/unlink website account\n"
        "6 - Buy Usage Credit add-ons\n"
        "7 - Tax tools, filing & quiz\n"
        "8 - Help\n\n"
        "Plans:\n"
        "S1/S2/S3 - Starter monthly/quarterly/yearly\n"
        "P1/P2/P3 - Professional monthly/quarterly/yearly\n"
        "B1/B2/B3 - Business monthly/quarterly/yearly\n\n"
        "Credits and billing:\n"
        "T10/T50/T100/T500 - Buy credit add-ons\n"
        "CR1 - Credit balance\n"
        "CR2 - Recent credit activity\n"
        "CR3 - AI credit deductions\n"
        "CR4 - Credit additions/top-ups\n"
        "PAY1 - Billing summary\n"
        "PAY2 - Payment history\n"
        "PAY3 - Latest payment status\n"
        "PAY4 <reference> - Verify payment reference\n"
        "PAY5 - Pending plan change\n"
        "PAY6 - Renewal/expiry date\n\n"
        "Tax tools and quiz:\n"
        "F1 - Calculator menu\n"
        "C1 - PAYE calculator\n"
        "C2 - Company Income Tax calculator\n"
        "C3 - VAT calculator\n"
        "C4 - Withholding Tax calculator\n"
        "C5 - Salary/net pay comparison\n"
        "C6 or Q1 - Tax quiz\n"
        "Q2 - Quiz categories\n"
        "Q3 - Quiz score\n"
        "Q4 - Last quiz review\n"
        "Q5 - Detailed saved quiz explanation\n\n"
        "Deadlines and history:\n"
        "D1 - Create reminder\n"
        "D2 - List reminders\n"
        "D3 - Delete reminder\n"
        "D4 - Update reminder\n"
        "H1 - Recent tax history\n"
        "H2 - Last tax answer\n\n"
        "Support, referral, filing, account:\n"
        "SUP1-SUP6 - Support tickets and support email\n"
        "R1-R6 - Referral code, link, stats, rewards, payout\n"
        "FT1-FT8 - Filing assistance and filing requests\n"
        "ACC1-ACC3 - Account/profile and linked channels\n"
        "SET1-SET3 - Settings guidance\n\n"
        "Navigation:\n"
        "0 or MENU - Main menu\n"
        "* or BACK - Go back\n"
        "CANCEL - Cancel current flow"
    )
    send_telegram_text(chat_id, msg)


def _send_credit_package_menu(chat_id: str, account_id: str, *, has_subscription: bool) -> None:
    if not has_subscription:
        send_telegram_text(
            chat_id,
            "💎 Usage Credit add-ons are available only to active paid subscribers.\n\n"
            "Reply 4 to view subscription plans or PAY1 to check billing summary.",
        )
        return

    send_telegram_text(chat_id, _topup_menu_text())


def _select_credit_package_number(text_lower: str) -> Optional[int]:
    return _topup_number_from_master_code(text_lower)


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
            "Reply 4 to view subscription plans.",
        )
        return True

    package = validate_package_number(package_num)
    if not package:
        send_telegram_text(chat_id, "❌ Invalid add-on package. Reply 6 to see packages again, then choose T10, T50, T100, or T500.")
        return True

    package_code = next((code for code, num in MASTER_TOPUP_CODE_TO_NUMBER.items() if num == package_num), "")
    reuse_msg = _recent_checkout_reuse_message(account_id, package_code=package_code) if package_code else None
    if reuse_msg:
        send_telegram_text(chat_id, reuse_msg)
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
    # Retained for backward internal calls. Batch 27D uses _handle_master_command
    # so that Telegram follows the WhatsApp master command registry.
    return _handle_master_command(
        chat_id=chat_id,
        account_id=account_id,
        tg_user_id=tg_user_id,
        text_raw=text_lower,
        linked=linked,
        has_subscription=has_subscription,
    )



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

    if text_lower in ["back", "*", "cancel"]:
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

    # WhatsApp master command registry must override stale conversational state.
    # Clear old state before handling the command; the command handler may set a new
    # state such as awaiting_email for a subscription checkout.
    user_states.pop(chat_id_str, None)
    if _handle_master_command(
        chat_id=chat_id_str,
        account_id=account_id,
        tg_user_id=tg_user_id,
        text_raw=text,
        linked=linked,
        has_subscription=has_subscription,
    ):
        return jsonify({"ok": True, "master_command": text.upper().split()[0]})


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
            send_telegram_text(chat_id_str, _master_plans_menu())
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

    if _looks_like_bad_command(text):
        send_telegram_text(chat_id_str, _invalid_command_text(text))
        return jsonify({"ok": True, "invalid_command": text})

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
            if answer:
                _log_telegram_history(account_id=account_id, question=text, answer=answer, result=result)
            send_telegram_text(chat_id_str, answer if answer else "I couldn't find an answer. Please try rephrasing.\n\nReply 0 for main menu.")
        else:
            send_telegram_text(chat_id_str, "Sorry, I encountered an error. Please try again.\n\nReply 0 for main menu.")
        return jsonify({"ok": True, "answered": True, "account_source": resolved.get("source")})
    except Exception as exc:
        logging.exception("Telegram webhook error: %s", exc)
        send_telegram_text(chat_id_str, "Sorry, I encountered an error. Please try again later.")
        return jsonify({"ok": True, "error_handled": True})
