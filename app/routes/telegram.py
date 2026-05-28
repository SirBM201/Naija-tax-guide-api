# app/routes/telegram.py
from __future__ import annotations

import logging
import os
import random
import re
import uuid
from datetime import datetime, timezone, timedelta
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

from app.services.referral_hub import (
    format_referral_code_message,
    format_referral_invite_message,
)

# Batch 27B1 fix: use the imported Supabase client object directly; do not call supabase().

bp = Blueprint("telegram", __name__)

TELEGRAM_ROUTE_VERSION = "2026-05-28-v36d-batch30d-deadline-mode-persistence-reread"

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
            "command_namespace": "whatsapp_master_registry_referral_filing_request_parity",
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
        "SUP1 - Create support ticket 🛟\nSUP3 - View open support tickets 🎫\nSUP4 - Reply to support ticket 📝\nSUP5 - Close support ticket ✅\n"
        "SUP2 - View latest support ticket 🎫\n"
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
        "🧰 *Tax Tools, Filing, Deadlines & Quiz*\n\n"
        "Quick filing/tool shortcuts:\n"
        "F1 - Calculator menu\n"
        "F2 - PAYE filing guide\n"
        "F3 - VAT filing guide\n"
        "F4 - CIT filing guide\n"
        "F5 - WHT guide\n"
        "F6 - Tax deadlines/calendar\n"
        "F7 - Filing checklist\n"
        "F8 - Back to main menu\n\n"
        "Calculators:\n"
        "C1 - PAYE calculator\n"
        "C2 - Company Income Tax calculator\n"
        "C3 - VAT calculator\n"
        "C4 - Withholding Tax calculator\n"
        "C5 - Salary / net pay estimate\n"
        "C6 - Tax quiz\n"
        "C7 - Tax calendar/deadlines\n"
        "C8 - Back to Tax Tools\n\n"
        "Deadline reminders:\n"
        "D1 - Create reminder\n"
        "D2 - List reminders\n"
        "D3 - Delete reminder\n"
        "D4 - Update reminder settings\n"
        "Example: D1 PAYE 2026-06-10 3 09:00 telegram\n\n"
        "Quiz:\n"
        "Q1 - Random quiz\n"
        "Q2 - Categories\n"
        "Q3 - Score\n"
        "Q4 - Review last answer\n"
        "Q5 - Detailed saved explanation\n\n"
        "Filing assistance:\n"
        "FT1 - Filing assistance menu\n"
        "FT2 - PAYE filing help\n"
        "FT3 - VAT filing help\n"
        "FT4 - CIT filing help\n"
        "FT5 - WHT filing help\n"
        "FT6 - Document checklist\n"
        "FT7 - Request human filing assistance\n"
        "FT8 - Filing status / latest request\n\n"
        "You can also type /paye, /vat, /cit, /deadlines, or quiz.\n"
        "Reply 0 for main menu."
    )
    send_telegram_text(chat_id, menu)


def _send_help(chat_id: str, *, linked: bool = False) -> None:
    option_5 = "Unlink website account" if linked else "Link website account"
    help_msg = (
        "*📖 Help Guide*\n\n"
        "• Ask tax questions: type your question naturally.\n"
        "  Example: What is PAYE tax?\n\n"
        "• Calculators: reply F1, or use C1-C5\n"
        "• Deadline reminders: use D1-D4\n"
        "• Tax quiz: use Q1-Q5\n"
        "• Check Usage Credits: reply 2 or CR1\n"
        "• View current plan: reply 3 or PAY1\n"
        "• View/upgrade plans: reply 4 then choose S1, P1, or B1\n"
        f"• {option_5}: reply 5 or ACC2\n"
        "• Buy Usage Credit add-ons: reply 6 then choose T10, T50, T100, or T500\n"
        "• Recent history: H1 / H2\n"
        "• Support: SUP1 create, SUP2 latest, SUP3 open, SUP4 reply, SUP5 close, SUP6 contact\n"
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


def _checkout_row_text(row: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in (
        "plan_code", "package_code", "product_code", "purpose", "type",
        "metadata", "description", "reference", "payment_reference",
        "provider_reference", "gateway_reference", "plan", "package",
        "channel_type", "source", "status", "payment_status",
        "transaction_status"
    ):
        value = row.get(key)
        if value is None:
            continue
        parts.append(_clean_text(value).lower())

    return " ".join(parts)


def _checkout_row_kind(row_text: str) -> str:
    """
    Batch 27D3:
    Detect whether an existing pending checkout row is most likely a
    subscription checkout or a usage-credit top-up checkout.

    This prevents a recent S1 subscription checkout from blocking T10 top-up,
    and prevents a recent top-up checkout from blocking a plan checkout.
    """
    text = (row_text or "").lower().replace("-", "_")

    topup_markers = (
        "topup",
        "top_up",
        "ai_topup",
        "credit_topup",
        "usage_credit",
        "usage credit",
        "credit add",
        "credit_add",
        "add_on",
        "addon",
        "add on",
        "t10",
        "t50",
        "t100",
        "t500",
    )

    subscription_markers = (
        "sub_",
        "subscription",
        "plan",
        "starter_monthly",
        "starter_quarterly",
        "starter_yearly",
        "professional_monthly",
        "professional_quarterly",
        "professional_yearly",
        "business_monthly",
        "business_quarterly",
        "business_yearly",
    )

    if any(marker in text for marker in topup_markers):
        return "topup"

    if any(marker in text for marker in subscription_markers):
        return "subscription"

    return "unknown"


def _topup_search_terms(package_code: str) -> list[str]:
    code = _clean_text(package_code).upper()
    credits = code.replace("T", "", 1) if code.startswith("T") else ""

    terms = [code.lower()]
    if credits:
        terms.extend(
            [
                f"topup_{credits}",
                f"top_up_{credits}",
                f"topup-{credits}",
                f"top-up-{credits}",
                f"{credits} credits",
                f"{credits}_credits",
                f"credit_{credits}",
            ]
        )

    return [t for t in terms if t]


def _checkout_fingerprint(account_id: str, *, kind: str, code: str) -> str:
    safe_kind = re.sub(r"[^a-z0-9_]+", "_", _clean_text(kind).lower()).strip("_")
    safe_code = re.sub(r"[^a-z0-9_]+", "_", _clean_text(code).lower()).strip("_")
    safe_account = re.sub(r"[^a-z0-9_-]+", "_", _clean_text(account_id).lower()).strip("_")
    return f"telegram:{safe_kind}:{safe_account}:{safe_code}"


def _checkout_lock_code_from_fingerprint(fingerprint: str) -> str:
    parts = _clean_text(fingerprint).split(":")
    return parts[-1].upper() if parts else "CHECKOUT"


def _checkout_lock_message(lock: dict[str, Any], *, kind: str, code: str) -> str:
    ref = _clean_text(
        lock.get("reference")
        or lock.get("payment_reference")
        or lock.get("provider_reference")
        or lock.get("gateway_reference")
        or "not shown"
    )
    url = _clean_text(lock.get("url") or lock.get("checkout_url") or lock.get("authorization_url"))
    label = _clean_text(code).upper() or _checkout_lock_code_from_fingerprint(_clean_text(lock.get("fingerprint")))
    kind_label = "top-up" if kind == "topup" else "subscription" if kind == "subscription" else "payment"

    if url.startswith("http"):
        return (
            "🧾 *Recent Checkout Found*\n\n"
            f"I found a recent pending {kind_label} checkout for {label}. "
            "To avoid duplicate payment records, use this existing checkout link:\n\n"
            f"{url}\n\n"
            f"Reference: {ref}\n\n"
            "If the link has expired, wait about 15 minutes and try again."
        )

    return (
        "🧾 *Recent Pending Checkout Found*\n\n"
        f"You already have a recent pending {kind_label} checkout for {label}.\n\n"
        "Please use the last payment link already shown above in this chat. "
        "To avoid duplicate payment records, I will not create another checkout immediately.\n\n"
        f"Reference: {ref}\n\n"
        "If you cannot find the link, wait about 15 minutes and try again, or contact support with SUP6."
    )


def _get_telegram_checkout_locks(tg_user_id: str) -> tuple[Optional[dict[str, Any]], dict[str, Any]]:
    identity = _get_telegram_identity(tg_user_id)
    if not identity:
        return None, {}

    metadata = identity.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}

    locks = metadata.get("telegram_checkout_locks") or {}
    if not isinstance(locks, dict):
        locks = {}

    return identity, locks


def _telegram_checkout_lock_message(
    *,
    tg_user_id: str,
    account_id: str,
    kind: str,
    code: str,
) -> Optional[str]:
    fingerprint = _checkout_fingerprint(account_id, kind=kind, code=code)
    _identity, locks = _get_telegram_checkout_locks(tg_user_id)
    lock = locks.get(fingerprint)

    if not isinstance(lock, dict):
        return None

    expires_at = _parse_dt(lock.get("expires_at"))
    if not expires_at or expires_at <= datetime.now(timezone.utc):
        return None

    return _checkout_lock_message(lock, kind=kind, code=code)


def _extract_checkout_info_from_result(result: dict[str, Any]) -> dict[str, str]:
    raw_parts: list[str] = []

    if isinstance(result, dict):
        for key in (
            "message",
            "authorization_url",
            "checkout_url",
            "payment_url",
            "url",
            "reference",
            "payment_reference",
            "provider_reference",
        ):
            value = result.get(key)
            if value:
                raw_parts.append(_clean_text(value))

    raw = "\n".join(raw_parts)

    url = ""
    match_url = re.search(r"https?://[^\s<>()]+", raw)
    if match_url:
        url = match_url.group(0).rstrip(".,)")

    reference = ""
    for pattern in (
        r"(?:Reference|Ref|reference|ref)\s*[:#-]\s*([A-Za-z0-9_.:-]+)",
        r"\b((?:SUB|TOP|TOPUP|NTG|TRX|PAY)[A-Za-z0-9_.:-]{6,})\b",
    ):
        match_ref = re.search(pattern, raw)
        if match_ref:
            reference = match_ref.group(1)
            break

    return {"url": url, "reference": reference}


def _record_telegram_checkout_lock(
    *,
    tg_user_id: str,
    account_id: str,
    kind: str,
    code: str,
    result: Optional[dict[str, Any]] = None,
) -> None:
    identity, locks = _get_telegram_checkout_locks(tg_user_id)
    if not identity:
        return

    identity_id = _clean_text(identity.get("id"))
    if not identity_id:
        return

    metadata = identity.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}

    if not isinstance(locks, dict):
        locks = {}

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=15)
    fingerprint = _checkout_fingerprint(account_id, kind=kind, code=code)
    info = _extract_checkout_info_from_result(result or {})

    # Keep the lock list small and remove expired locks.
    cleaned_locks: dict[str, Any] = {}
    for key, value in locks.items():
        if not isinstance(value, dict):
            continue
        expiry = _parse_dt(value.get("expires_at"))
        if expiry and expiry > now:
            cleaned_locks[key] = value

    cleaned_locks[fingerprint] = {
        "fingerprint": fingerprint,
        "kind": kind,
        "code": _clean_text(code).upper(),
        "reference": info.get("reference") or "",
        "url": info.get("url") or "",
        "created_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
        "source": "telegram_batch_27d4",
    }

    metadata["telegram_checkout_locks"] = cleaned_locks
    metadata["last_checkout_fingerprint"] = fingerprint

    try:
        # Batch 27D6:
        # The current channel_identities table does not expose updated_at.
        # Updating metadata + updated_at caused PGRST204 and prevented the
        # checkout fingerprint lock from being saved. Save metadata only.
        supabase.table("channel_identities").update(
            {
                "metadata": metadata,
            }
        ).eq("id", identity_id).execute()
    except Exception:
        logging.exception("Telegram checkout fingerprint metadata update failed")



def _plan_search_terms(plan_code: str) -> list[str]:
    code = _clean_text(plan_code).lower()
    terms = [code]

    if code:
        terms.append(f"sub_{code}")
        terms.append(f"subscription_{code}")

    return [t for t in terms if t]


def _checkout_has_exact_term(row_text: str, term: str) -> bool:
    """
    Batch 27D5:
    Exact checkout matching.

    Avoid substring mistakes such as:
      - T50 matching T500
      - credit_50 matching credit_500
      - S1-style plan checks accidentally matching unrelated text.
    """
    text = _clean_text(row_text).lower()
    value = _clean_text(term).lower()

    if not text or not value:
        return False

    # For phrases, normalize whitespace and use boundary-safe regex.
    if " " in value:
        escaped = r"\s+".join(re.escape(part) for part in value.split())
        return re.search(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", text, flags=re.I) is not None

    return re.search(rf"(?<![a-z0-9]){re.escape(value)}(?![a-z0-9])", text, flags=re.I) is not None


def _checkout_has_any_exact_term(row_text: str, terms: list[str]) -> bool:
    return any(_checkout_has_exact_term(row_text, term) for term in terms)


def _recent_checkout_reuse_message(
    account_id: str,
    *,
    tg_user_id: str = "",
    plan_code: str = "",
    package_code: str = "",
) -> Optional[str]:
    """
    Batch 27D5:
    Exact checkout fingerprint guard.

    Blocking is now allowed only when:
      1. an exact short-lived fingerprint lock exists; or
      2. the legacy paystack_transactions row contains an exact matching
         plan/top-up term.

    Removed broad same-kind fallback because it caused:
      - T50 to block T500;
      - T500 to block T50;
      - possible S1/P1/B1 cross-blocking.
    """
    requested_kind = "subscription" if plan_code else "topup" if package_code else ""
    requested_code = plan_code or package_code
    requested_label = (requested_code or "checkout").upper()

    # 1. Exact fingerprint lock check. This is the reliable guard for new
    # checkout records created by Batch 27D4+.
    if tg_user_id and requested_kind and requested_code:
        lock_message = _telegram_checkout_lock_message(
            tg_user_id=tg_user_id,
            account_id=account_id,
            kind=requested_kind,
            code=requested_code,
        )
        if lock_message:
            return lock_message

    # 2. Legacy paystack_transactions scan. Exact-code only.
    # No same-kind fallback is allowed here.
    if requested_kind == "subscription":
        requested_terms = _plan_search_terms(plan_code)
    elif requested_kind == "topup":
        requested_terms = _topup_search_terms(package_code)
    else:
        requested_terms = []

    rows = _rows_for_account("paystack_transactions", account_id, limit=20)

    for row in rows:
        if not _row_is_open_checkout(row) or not _row_recent_enough(row, seconds=900):
            continue

        row_text = _checkout_row_text(row)
        row_kind = _checkout_row_kind(row_text)

        # Hard separation:
        # known subscription rows cannot block top-up rows;
        # known top-up rows cannot block subscription rows.
        if requested_kind and row_kind not in {requested_kind, "unknown"}:
            continue

        # Exact match only. This prevents T50 from matching T500 and prevents
        # a generic recent top-up from blocking another top-up package.
        if not _checkout_has_any_exact_term(row_text, requested_terms):
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
                f"I found a recent pending {requested_kind or 'payment'} checkout for {requested_label}. "
                "To avoid duplicate payment records, use this existing checkout link:\n\n"
                f"{url}\n\n"
                f"Reference: {ref}\n\n"
                "If the link has expired, wait a few minutes or contact support."
            )

        return (
            "🧾 *Recent Pending Checkout Found*\n\n"
            f"You already have a recent pending {requested_kind or 'payment'} checkout for {requested_label}.\n\n"
            "Please use the last payment link already shown above in this chat. "
            "To avoid duplicate payment records, I will not create another checkout immediately.\n\n"
            f"Reference: {ref}\n\n"
            "If you cannot find the link, wait about 15 minutes and try again, or contact support with SUP6."
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
    reuse_msg = (
        _recent_checkout_reuse_message(account_id, tg_user_id=tg_user_id, plan_code=plan_code)
        if plan_code
        else None
    )
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
        if result.get("ok") and plan_code:
            _record_telegram_checkout_lock(
                tg_user_id=tg_user_id,
                account_id=account_id,
                kind="subscription",
                code=plan_code,
                result=result,
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

    credit_line = f"Credits used: {credits}" if credits else "Credits used: 0 or not charged"
    body = (
        "📌 *Last Tax Answer*\n\n"
        f"Date: {created}\n"
        f"Source: {source}\n"
        f"{credit_line}\n\n"
        f"Question:\n{question}\n\n"
        f"Answer:\n{answer}\n\n"
        "Reply H1 for recent history or 0 for main menu."
    )
    send_telegram_text(chat_id, _clip_text(body))


def _history_key(value: Any) -> str:
    text = re.sub(r"\s+", " ", _clean_text(value).lower()).strip()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text[:180]


def _safe_insert_row(table: str, payload: dict[str, Any]) -> dict[str, Any]:
    ok, resp, err = _safe_exec(supabase.table(table).insert(payload))
    return {"ok": ok, "resp": resp, "error": err}


def _log_telegram_history(*, account_id: str, question: str, answer: str, result: dict[str, Any]) -> dict[str, Any]:
    """
    Batch 28A:
    Robust Telegram qa_history logging.

    The earlier Telegram history insert had one payload only and failed silently
    when optional columns did not exist. This version follows the safer WhatsApp
    pattern: try richer payload first, then simpler fallbacks.
    """
    meta = result.get("meta") if isinstance(result, dict) and isinstance(result.get("meta"), dict) else {}

    try:
        credits_consumed = int(meta.get("credits_consumed") or meta.get("credit_cost") or 0)
    except Exception:
        credits_consumed = 0

    source = _clean_text(result.get("source") if isinstance(result, dict) else "")
    mode = _clean_text(result.get("mode") if isinstance(result, dict) else "")
    from_cache = bool(source == "database" or mode == "direct_cache" or source == "cache" or mode == "cache")
    usage_charged = bool(meta.get("usage_charged") is True or credits_consumed > 0)
    now_iso = _utc_now_iso()
    normalized_question = re.sub(r"\s+", " ", _clean_text(question)).strip()

    payloads = [
        {
            "account_id": account_id or None,
            "question": _clip_text(question, 5000),
            "answer": _clip_text(answer, 20000),
            "lang": "en",
            "source": "telegram",
            "provider": "telegram",
            "from_cache": from_cache,
            "canonical_key": _history_key(question),
            "normalized_question": normalized_question,
            "plan_code": _clean_text(meta.get("plan_code")) or None,
            "credits_consumed": credits_consumed,
            "usage_charged": usage_charged,
            "channel": "telegram",
            "created_at": now_iso,
            "updated_at": now_iso,
        },
        {
            "account_id": account_id or None,
            "question": _clip_text(question, 5000),
            "answer": _clip_text(answer, 20000),
            "lang": "en",
            "source": "telegram",
            "provider": "telegram",
            "from_cache": from_cache,
            "credits_consumed": credits_consumed,
            "usage_charged": usage_charged,
            "channel": "telegram",
            "created_at": now_iso,
        },
        {
            "account_id": account_id or None,
            "question": _clip_text(question, 5000),
            "answer": _clip_text(answer, 20000),
            "source": "telegram",
            "channel": "telegram",
            "created_at": now_iso,
        },
        {
            "account_id": account_id or None,
            "question": _clip_text(question, 5000),
            "answer": _clip_text(answer, 20000),
            "created_at": now_iso,
        },
        {
            "question": _clip_text(question, 5000),
            "answer": _clip_text(answer, 20000),
            "created_at": now_iso,
        },
    ]

    errors: list[str] = []
    for idx, payload in enumerate(payloads):
        inserted = _safe_insert_row("qa_history", payload)
        if inserted.get("ok"):
            return {"ok": True, "mode": f"telegram_history_payload_{idx}"}
        errors.append(str(inserted.get("error")))

    logging.warning("Telegram qa_history insert failed: %s", errors[:3])
    return {"ok": False, "error": "telegram_history_insert_failed", "errors": errors[:3]}


def _telegram_answer_credit_note(result: dict[str, Any]) -> str:
    if not isinstance(result, dict):
        return ""

    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    result_ok = bool(result.get("ok") is True)

    if result_ok and meta.get("usage_charged") is True:
        used = meta.get("credits_consumed") or meta.get("credit_cost") or 1
        balance = meta.get("credits_left")
        if balance is None:
            balance = meta.get("balance")
        balance_text = f" Balance: {balance}." if balance is not None else ""
        return f"\n\n💎 Credit used: {used}.{balance_text}"

    source = _clean_text(result.get("source"))
    mode = _clean_text(result.get("mode"))
    if result_ok and (source in {"database", "cache"} or mode in {"direct_cache", "cache"}):
        return "\n\n✅ Served from saved database/cache. No new credit charged."

    error_code = _clean_text(result.get("error"))
    if not result_ok and error_code in {"paid_plan_required", "insufficient_credits", "no_credits", "credit_balance_empty"}:
        return "\n\nNo credit was charged for this blocked request. Reply CR1 to check credits or 6 to buy Usage Credits."

    return ""


def _telegram_answer_text(result: dict[str, Any]) -> str:
    if not isinstance(result, dict):
        return "I could not generate an answer right now. Please try again shortly."

    answer = _clean_text(result.get("answer") or result.get("message"))
    if not answer:
        if result.get("ok") is True:
            answer = "I couldn't find a clear answer. Please try rephrasing your question."
        else:
            answer = "I could not generate an answer right now. Please try again shortly."

    return _clip_text(answer + _telegram_answer_credit_note(result) + "\n\nReply H1 for history or 0 for main menu.", 3900)


def _handle_telegram_tax_question(
    *,
    chat_id: str,
    account_id: str,
    tg_user_id: str,
    question: str,
    account_source: str = "",
) -> dict[str, Any]:
    """
    Batch 28A:
    Central Telegram AI ask handler.

    Guarantees:
      - Uses resolved channel_identity/account_id.
      - Sends provider/provider_user_id to ask_guarded.
      - Lets ask_guarded decide cache/library/AI and credit charging.
      - Logs successful answers to qa_history.
      - Adds clear credit/cache note to the Telegram response.
      - Failed answers are not logged as successful history.
    """
    before_balance = None
    try:
        before_balance = get_credit_balance(account_id)
    except Exception:
        before_balance = None

    result = ask_guarded(
        {
            "account_id": account_id,
            "question": question,
            "lang": "en",
            "channel": "telegram",
            "provider": "telegram",
            "provider_user_id": tg_user_id,
            "action_code": "ai_tax_answer",
            "before_balance": before_balance,
        }
    )

    if not isinstance(result, dict):
        result = {"ok": False, "message": "I could not generate an answer right now. Please try again shortly."}

    answer = _clean_text(result.get("answer") or result.get("message"))
    if result.get("ok") is True and answer:
        _log_telegram_history(account_id=account_id, question=question, answer=answer, result=result)

    send_telegram_text(chat_id, _telegram_answer_text(result))

    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    return {
        "ok": True,
        "answered": True,
        "result_ok": bool(result.get("ok") is True),
        "account_source": account_source,
        "usage_charged": meta.get("usage_charged"),
        "credits_consumed": meta.get("credits_consumed"),
        "source": result.get("source"),
        "mode": result.get("mode"),
    }


def _subscription_row(account_id: str) -> Optional[dict[str, Any]]:
    try:
        resp = supabase.table("user_subscriptions").select("*").eq("account_id", account_id).order("created_at", desc=True).limit(1).execute()
        return _first(resp)
    except Exception:
        return None


def _credit_balance_value(balance: Any) -> int:
    """
    Return the user's current Usage Credit balance from different service/table
    payload shapes.

    Batch 28H fix:
    CR1 can show the correct balance while PAY1/ACC1 show 0 if the service
    returns a key such as credits or available_credits instead of balance.
    Keep all Telegram balance displays aligned with CR1.
    """
    if isinstance(balance, (int, float)):
        try:
            return int(balance)
        except Exception:
            return 0

    if not isinstance(balance, dict):
        return 0

    for key in (
        "balance",
        "credits",
        "credit_balance",
        "available_credits",
        "remaining_credits",
        "usage_credits",
        "total_credits",
        "remaining",
    ):
        if key in balance and balance.get(key) not in (None, ""):
            try:
                return int(float(str(balance.get(key)).replace(",", "")))
            except Exception:
                pass

    for key in ("row", "data", "balance_row", "credit_balance_row"):
        nested = balance.get(key)
        if isinstance(nested, dict):
            value = _credit_balance_value(nested)
            if value != 0:
                return value
        if isinstance(nested, list) and nested and isinstance(nested[0], dict):
            value = _credit_balance_value(nested[0])
            if value != 0:
                return value

    return 0


def _billing_summary_text(account_id: str) -> str:
    sub = _subscription_row(account_id)
    balance = get_credit_balance(account_id)
    bal_value = _credit_balance_value(balance)

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


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value or 0))
    except Exception:
        return default


def _telegram_q5_credit_activity_rows(account_id: str, limit: int = 5) -> list[dict[str, Any]]:
    """
    Batch 28D2:
    Fallback audit view for Q5.

    Q5 already updates tax_quiz_attempts successfully after the credit debit.
    If credit_usage_logs has an older/narrower schema, CR3 can still show Q5
    deductions from tax_quiz_attempts instead of saying no activity exists.
    """
    rows: list[dict[str, Any]] = []

    try:
        resp = (
            supabase.table("tax_quiz_attempts")
            .select("*")
            .eq("account_id", account_id)
            .eq("q5_explanation_used", True)
            .order("q5_explained_at", desc=True)
            .limit(max(1, min(limit, 10)))
            .execute()
        )
        rows = _rows(resp)
    except Exception:
        try:
            resp = (
                supabase.table("tax_quiz_attempts")
                .select("*")
                .eq("account_id", account_id)
                .limit(max(1, min(limit, 10)))
                .execute()
            )
            rows = [
                r for r in _rows(resp)
                if bool(r.get("q5_explanation_used")) or _safe_int(r.get("credits_charged")) > 0
            ]
        except Exception:
            rows = []

    normalized: list[dict[str, Any]] = []
    for row in rows[:limit]:
        charged = _safe_int(row.get("credits_charged"), 1) or 1
        created = row.get("q5_explained_at") or row.get("answered_at") or row.get("updated_at") or row.get("created_at")
        normalized.append(
            {
                "id": f"q5:{_clean_text(row.get('id'))}",
                "account_id": account_id,
                "action_code": "quiz_q5_saved_explanation",
                "description": "Telegram Q5 detailed saved quiz explanation",
                "credits_delta": -abs(charged),
                "amount": -abs(charged),
                "created_at": created,
                "source": "tax_quiz_attempts",
            }
        )

    return normalized


def _credit_activity_row_key(row: dict[str, Any]) -> str:
    return _clean_text(row.get("id") or row.get("reference") or row.get("created_at") or row.get("description"))


def _combined_credit_activity_rows(account_id: str, *, mode: str, limit: int = 8) -> list[dict[str, Any]]:
    base_rows = _rows_for_account("credit_usage_logs", account_id, limit=limit)
    q5_rows = _telegram_q5_credit_activity_rows(account_id, limit=limit)

    rows: list[dict[str, Any]] = []

    if mode == "ai":
        for row in base_rows:
            text = _clean_text(row.get("action_code") or row.get("description")).lower()
            if "ai" in text or "q5" in text or _safe_int(row.get("credits_delta") or row.get("amount")) < 0:
                rows.append(row)
        rows.extend(q5_rows)
    elif mode == "additions":
        for row in base_rows:
            text = _clean_text(row.get("action_code") or row.get("description")).lower()
            if "top" in text or _safe_int(row.get("credits_delta") or row.get("amount")) > 0:
                rows.append(row)
    else:
        rows = list(base_rows) + q5_rows

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        key = _credit_activity_row_key(row)
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        deduped.append(row)

    def sort_key(row: dict[str, Any]) -> str:
        return _clean_text(row.get("created_at") or row.get("updated_at") or row.get("q5_explained_at"))

    deduped.sort(key=sort_key, reverse=True)
    return deduped[:limit]


def _send_credit_rows(chat_id: str, account_id: str, *, mode: str) -> None:
    if mode == "ai":
        rows = _combined_credit_activity_rows(account_id, mode="ai", limit=8)
        title = "📉 *AI / Usage Credit Deductions*"
    elif mode == "additions":
        rows = _combined_credit_activity_rows(account_id, mode="additions", limit=8)
        title = "➕ *Credit Additions / Top-ups*"
    else:
        rows = _combined_credit_activity_rows(account_id, mode="all", limit=8)
        title = "💎 *Recent Credit Activity*"

    if not rows:
        balance = get_credit_balance(account_id)
        bal_value = _credit_balance_value(balance)
        send_telegram_text(chat_id, f"{title}\n\nNo matching credit activity found yet.\n\nCurrent balance: {bal_value}\n\nReply CR1 for balance or 0 for main menu.")
        return

    lines = [title, ""]
    for idx, row in enumerate(rows[:5], 1):
        desc = row.get("description") or row.get("action_code") or "Credit activity"
        delta = row.get("credits_delta")
        if delta is None or delta == "":
            delta = row.get("amount") or row.get("credits") or row.get("credit_delta") or row.get("delta") or ""
        created = row.get("created_at") or row.get("updated_at") or row.get("q5_explained_at")
        source = _clean_text(row.get("source"))
        lines.append(f"{idx}. {desc}")
        if delta != "":
            lines.append(f"   Credits: {delta}")
        if source == "tax_quiz_attempts":
            lines.append("   Source: Quiz Q5 saved explanation")
        lines.append(f"   Date: {_date_short(created)}")
        lines.append("")

    lines.append("Reply CR1 for balance, T10/T50/T100/T500 for top-up, or 0 for menu.")
    send_telegram_text(chat_id, _clip_text("\n".join(lines)))


def _send_support_menu(chat_id: str) -> None:
    send_telegram_text(
        chat_id,
        "🛟 *Support Centre*\n\n"
        "SUP1 - Create support ticket\n"
        "SUP2 - View latest ticket\n"
        "SUP3 - View open tickets\n"
        "SUP4 - Reply/update latest ticket\n"
        "SUP5 - Close latest ticket\n"
        "SUP6 - Contact support / escalation guide\n\n"
        "Quick examples:\n"
        "SUP1 I paid but my plan has not updated. Reference NTG-...\n"
        "SUP4 Please note that my Paystack reference is NTG-...\n"
        "SUP4 2 I have attached the missing information.\n"
        "SUP5\n"
        "SUP5 1 Resolved now, thank you.\n\n"
        "Reply 0 for main menu.",
    )


def _send_support_email(chat_id: str) -> None:
    send_telegram_text(
        chat_id,
        "📧 *Contact Support / Escalation Guide*\n\n"
        "Email: support@naijataxguides.com\n\n"
        "For faster help, include:\n"
        "• Your Telegram ID\n"
        "• Registered email/phone\n"
        "• Ticket reference if available\n"
        "• Payment reference if it is a billing issue\n"
        "• A short description of the issue\n\n"
        "Telegram commands:\n"
        "SUP1 your issue - create ticket\n"
        "SUP2 - view latest ticket\n"
        "SUP3 - view open tickets\n"
        "SUP4 your update - reply to latest open ticket\n"
        "SUP5 - close latest open ticket",
    )


def _support_ref(row: dict[str, Any]) -> str:
    return _clean_text(row.get("ticket_id") or row.get("reference") or row.get("id") or "No reference")


def _support_status(row: dict[str, Any]) -> str:
    return _clean_text(row.get("status") or row.get("admin_status") or "open").lower()


def _support_is_open(row: dict[str, Any]) -> bool:
    status = _support_status(row)
    return status not in {"closed", "resolved", "cancelled", "canceled", "done", "completed"}


def _support_subject(row: dict[str, Any]) -> str:
    return _clean_text(
        row.get("subject")
        or row.get("title")
        or row.get("message")
        or row.get("description")
        or "Support ticket"
    )


def _support_user_message(row: dict[str, Any]) -> str:
    return _clean_text(row.get("message") or row.get("description") or row.get("body") or "")


def _support_admin_note(row: dict[str, Any]) -> str:
    for key in (
        "admin_reply",
        "admin_note",
        "admin_notes",
        "support_reply",
        "response",
        "resolution",
        "resolution_note",
        "resolution_notes",
        "last_admin_reply",
    ):
        value = _clean_text(row.get(key))
        if value:
            return value
    return ""


def _support_rows(account_id: str, *, limit: int = 10) -> list[dict[str, Any]]:
    try:
        resp = (
            supabase.table("support_tickets")
            .select("*")
            .eq("account_id", account_id)
            .order("created_at", desc=True)
            .limit(max(1, min(limit, 20)))
            .execute()
        )
        return _rows(resp)
    except Exception:
        try:
            resp = (
                supabase.table("support_tickets")
                .select("*")
                .eq("account_id", account_id)
                .limit(max(1, min(limit, 20)))
                .execute()
            )
            return _rows(resp)
        except Exception:
            return []


def _support_open_rows(account_id: str, *, limit: int = 10) -> list[dict[str, Any]]:
    rows = _support_rows(account_id, limit=limit)
    return [row for row in rows if _support_is_open(row)]


def _support_ticket_line(row: dict[str, Any], idx: int) -> list[str]:
    ref = _support_ref(row)
    status = _support_status(row).title()
    subject = _history_excerpt(_support_subject(row), 120)
    created = row.get("created_at")
    updated = row.get("updated_at")
    priority = _clean_text(row.get("priority"))
    category = _clean_text(row.get("category"))
    admin_note = _support_admin_note(row)

    lines = [
        f"{idx}. {ref}",
        f"   Status: {status}",
        f"   Subject: {subject}",
        f"   Created: {_date_short(created)}",
    ]

    if updated:
        lines.append(f"   Last update: {_date_short(updated)}")
    if category:
        lines.append(f"   Category: {category}")
    if priority:
        lines.append(f"   Priority: {priority}")
    if admin_note:
        lines.append(f"   Admin note: {_history_excerpt(admin_note, 140)}")

    return lines


def _send_support_tickets(chat_id: str, account_id: str, *, mode: str = "latest") -> None:
    if mode == "open":
        rows = _support_open_rows(account_id, limit=10)
        title = "🎫 *Open Support Tickets*"
        empty = (
            "🎫 *Open Support Tickets*\n\n"
            "No open support ticket found.\n\n"
            "Reply SUP1 followed by your issue to create one."
        )
    elif mode == "all":
        rows = _support_rows(account_id, limit=5)
        title = "🎫 *My Support Tickets*"
        empty = (
            "🎫 *Support Tickets*\n\n"
            "No support ticket found yet.\n\n"
            "Reply SUP1 followed by your issue to create one."
        )
    else:
        rows = _support_rows(account_id, limit=1)
        title = "🎫 *Latest Support Ticket*"
        empty = (
            "🎫 *Latest Support Ticket*\n\n"
            "No support ticket found yet.\n\n"
            "Reply SUP1 followed by your issue to create one."
        )

    if not rows:
        send_telegram_text(chat_id, empty)
        return

    lines = [title, ""]
    for idx, row in enumerate(rows, 1):
        lines.extend(_support_ticket_line(row, idx))
        lines.append("")

    if mode == "open":
        lines.append("Reply SUP4 1 your message to add a reply, SUP5 1 to close ticket, or 0 for menu.")
    else:
        lines.append("Reply SUP4 with your message to add a note, SUP5 to close latest/open ticket, SUP3 to view open tickets, or 0 for menu.")

    send_telegram_text(chat_id, _clip_text("\n".join(lines)))


def _support_find_ticket(account_id: str, selector: str = "", *, prefer_open: bool = True) -> tuple[Optional[dict[str, Any]], list[dict[str, Any]]]:
    rows = _support_rows(account_id, limit=10)
    if not rows:
        return None, rows

    selector = _clean_text(selector)
    open_rows = [row for row in rows if _support_is_open(row)]

    if selector:
        if selector.isdigit():
            idx = int(selector) - 1
            source_rows = open_rows if prefer_open and open_rows else rows
            if 0 <= idx < len(source_rows):
                return source_rows[idx], rows
            return None, rows

        selector_norm = selector.lower()
        for row in rows:
            ref = _support_ref(row).lower()
            row_id = _clean_text(row.get("id")).lower()
            if selector_norm == ref or selector_norm == row_id:
                return row, rows
            if ref and selector_norm in ref:
                return row, rows

    if prefer_open and open_rows:
        return open_rows[0], rows

    return rows[0], rows


def _support_update_where(row: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    row_id = _clean_text(row.get("id"))
    ticket_ref = _support_ref(row)

    attempts = [
        dict(payload),
        {k: v for k, v in payload.items() if k not in {"updated_at", "metadata"}},
        {k: v for k, v in payload.items() if k in {"message", "status"}},
        {k: v for k, v in payload.items() if k == "status"},
    ]

    errors: list[str] = []
    seen: set[str] = set()

    for candidate in attempts:
        cleaned = {k: v for k, v in candidate.items() if v is not None}
        if not cleaned:
            continue
        signature = repr(sorted(cleaned.keys()))
        if signature in seen:
            continue
        seen.add(signature)

        if row_id:
            ok, resp, err = _safe_exec(supabase.table("support_tickets").update(cleaned).eq("id", row_id))
            if ok:
                return {"ok": True, "data": _rows(resp), "mode": signature, "where": "id"}
            errors.append(str(err))

        if ticket_ref and ticket_ref != "No reference":
            ok, resp, err = _safe_exec(supabase.table("support_tickets").update(cleaned).eq("ticket_id", ticket_ref))
            if ok:
                return {"ok": True, "data": _rows(resp), "mode": signature, "where": "ticket_id"}
            errors.append(str(err))

    return {"ok": False, "error": errors[-1] if errors else "update_failed", "errors": errors[:3]}


def _support_insert_ticket(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Batch 28E1:
    Reduce SUP1 support_tickets insert retry noise.

    Batch 28E logs showed:
      1st insert payload -> 400 Bad Request
      2nd insert payload -> 201 Created

    The accepted payload contained:
      ticket_id, account_id, subject, message, status, created_at, updated_at

    Therefore we now try that accepted payload first.
    """
    accepted_payload = {
        "ticket_id": payload.get("ticket_id"),
        "account_id": payload.get("account_id"),
        "subject": payload.get("subject"),
        "message": payload.get("message"),
        "status": payload.get("status"),
        "created_at": payload.get("created_at"),
        "updated_at": payload.get("updated_at"),
    }

    # Keep narrower/wider fallbacks, but place them after the known accepted schema.
    payload_attempts = [
        accepted_payload,
        {
            "ticket_id": payload.get("ticket_id"),
            "account_id": payload.get("account_id"),
            "subject": payload.get("subject"),
            "message": payload.get("message"),
            "status": payload.get("status"),
            "created_at": payload.get("created_at"),
        },
        {
            "ticket_id": payload.get("ticket_id"),
            "account_id": payload.get("account_id"),
            "message": payload.get("message"),
            "status": payload.get("status"),
            "created_at": payload.get("created_at"),
        },
        {
            "ticket_id": payload.get("ticket_id"),
            "account_id": payload.get("account_id"),
            "category": payload.get("category"),
            "priority": payload.get("priority"),
            "subject": payload.get("subject"),
            "message": payload.get("message"),
            "status": payload.get("status"),
            "channel": payload.get("channel"),
            "source": payload.get("source"),
            "metadata": payload.get("metadata"),
            "created_at": payload.get("created_at"),
            "updated_at": payload.get("updated_at"),
        },
        dict(payload),
    ]

    errors: list[str] = []
    seen: set[str] = set()

    for candidate in payload_attempts:
        cleaned = {k: v for k, v in candidate.items() if v is not None}
        signature = repr(sorted(cleaned.keys()))
        if signature in seen:
            continue
        seen.add(signature)

        ok, resp, err = _safe_exec(supabase.table("support_tickets").insert(cleaned))
        if ok:
            return {"ok": True, "data": _rows(resp), "mode": signature}
        errors.append(str(err))

    return {"ok": False, "error": errors[-1] if errors else "insert_failed", "errors": errors[:3]}


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

    payload = {
        "ticket_id": ticket_id,
        "account_id": account_id,
        "category": "general",
        "priority": "normal",
        "subject": details[:120],
        "message": details,
        "status": "open",
        "metadata": {"created_from": "telegram", "command": "SUP1"},
        "created_at": now,
        "updated_at": now,
    }

    result = _support_insert_ticket(payload)
    if result.get("ok"):
        rows = result.get("data") if isinstance(result.get("data"), list) else []
        saved_row = rows[0] if rows and isinstance(rows[0], dict) else payload
        ref = _support_ref(saved_row) or ticket_id
        send_telegram_text(
            chat_id,
            f"✅ *Support Ticket Created*\n\n"
            f"Ticket: {ref}\n"
            f"Status: Open\n\n"
            "Our support team will review it.\n\n"
            "Reply SUP2 to view latest ticket, SUP4 to add a reply, or 0 for main menu.",
        )
        return

    logging.warning("Telegram support ticket insert failed: %s", result.get("error"))
    send_telegram_text(
        chat_id,
        "⚠️ I could not save the support ticket automatically.\n\n"
        "Please email support@naijataxguides.com and include this reference:\n"
        f"{ticket_id}\n\n"
        "Your message:\n"
        f"{_clip_text(details, 600)}",
    )


def _parse_support_selector_and_message(text_raw: str, command: str) -> tuple[str, str]:
    body = re.sub(rf"^{re.escape(command)}\b", "", _clean_text(text_raw), flags=re.I).strip()
    if not body:
        return "", ""

    parts = body.split(maxsplit=1)
    first = parts[0].strip()
    rest = parts[1].strip() if len(parts) > 1 else ""

    # Numeric selector: SUP4 2 message / SUP5 1
    if first.isdigit():
        return first, rest

    # Reference selector: SUP4 NTG-TG-20260527010101 message
    looks_like_ref = (
        len(first) >= 8
        and bool(re.search(r"[A-Z0-9]", first, flags=re.I))
        and ("-" in first or first.upper().startswith("NTG") or first.count("-") >= 2)
    )
    if looks_like_ref and rest:
        return first, rest

    return "", body


def _append_support_reply(chat_id: str, account_id: str, text_raw: str) -> None:
    selector, reply = _parse_support_selector_and_message(text_raw, "SUP4")

    if not reply:
        send_telegram_text(
            chat_id,
            "📝 *Reply to Support Ticket*\n\n"
            "Send SUP4 followed by your message.\n\n"
            "Examples:\n"
            "SUP4 Please note that my Paystack reference is NTG-...\n"
            "SUP4 2 I have attached the missing information.\n\n"
            "Use SUP3 to view open ticket numbers.",
        )
        return

    row, rows = _support_find_ticket(account_id, selector, prefer_open=True)
    if not row:
        send_telegram_text(
            chat_id,
            "🎫 I could not find an open ticket to update.\n\n"
            "Reply SUP1 followed by your issue to create a new ticket, or SUP3 to view open tickets.",
        )
        return

    if not _support_is_open(row):
        send_telegram_text(
            chat_id,
            "⚠️ That ticket is already closed/resolved.\n\n"
            "Reply SUP1 followed by your new issue to create a fresh ticket.",
        )
        return

    old_message = _support_user_message(row)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    appended = (
        f"{old_message}\n\n"
        f"--- Telegram user update ({timestamp}) ---\n"
        f"{reply}"
    ).strip()
    appended = _clip_text(appended, 6000)

    payload = {
        "message": appended,
        "status": "open",
        "updated_at": _utc_now_iso(),
        "metadata": {
            "last_user_reply_source": "telegram",
            "last_user_reply_at": _utc_now_iso(),
        },
    }

    result = _support_update_where(row, payload)
    if not result.get("ok"):
        send_telegram_text(
            chat_id,
            "⚠️ I could not save your reply automatically.\n\n"
            f"Ticket: {_support_ref(row)}\n"
            "Please email support@naijataxguides.com with your update if urgent.",
        )
        return

    send_telegram_text(
        chat_id,
        "✅ *Support Ticket Updated*\n\n"
        f"Ticket: {_support_ref(row)}\n"
        "Status: Open\n\n"
        f"Your update:\n{_clip_text(reply, 700)}\n\n"
        "Reply SUP2 to view latest ticket, SUP5 to close it, or 0 for menu.",
    )


def _close_support_ticket(chat_id: str, account_id: str, text_raw: str) -> None:
    selector, note = _parse_support_selector_and_message(text_raw, "SUP5")

    row, rows = _support_find_ticket(account_id, selector, prefer_open=True)
    if not row:
        send_telegram_text(
            chat_id,
            "🎫 I could not find an open ticket to close.\n\n"
            "Reply SUP3 to view open tickets or SUP1 followed by your issue to create one.",
        )
        return

    if not _support_is_open(row):
        send_telegram_text(
            chat_id,
            f"✅ Ticket already closed/resolved.\n\nTicket: {_support_ref(row)}\n\nReply SUP3 to view open tickets or 0 for menu.",
        )
        return

    old_message = _support_user_message(row)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    closure_note = note or "Closed by user from Telegram."
    appended = (
        f"{old_message}\n\n"
        f"--- Ticket closed by user ({timestamp}) ---\n"
        f"{closure_note}"
    ).strip()
    appended = _clip_text(appended, 6000)

    payload = {
        "message": appended,
        "status": "closed",
        "updated_at": _utc_now_iso(),
        "metadata": {
            "closed_from": "telegram",
            "closed_at": _utc_now_iso(),
        },
    }

    result = _support_update_where(row, payload)
    if not result.get("ok"):
        send_telegram_text(
            chat_id,
            "⚠️ I could not close that ticket automatically.\n\n"
            f"Ticket: {_support_ref(row)}\n"
            "Please email support@naijataxguides.com if it must be closed urgently.",
        )
        return

    send_telegram_text(
        chat_id,
        "✅ *Support Ticket Closed*\n\n"
        f"Ticket: {_support_ref(row)}\n"
        "Status: Closed\n\n"
        "Reply SUP3 to view open tickets, SUP1 to create a new ticket, or 0 for menu.",
    )


def _referral_frontend_base() -> str:
    base = (
        os.getenv("FRONTEND_BASE_URL")
        or os.getenv("WEB_APP_URL")
        or os.getenv("APP_BASE_URL")
        or "https://www.naijataxguides.com"
    )
    return base.rstrip("/")


def _referral_fallback_code(account_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]", "", _clean_text(account_id)).upper()
    return f"NTG{(cleaned or 'USER')[:8]}"


def _referral_link_for_code(code: str) -> str:
    return f"{_referral_frontend_base()}/?ref={_clean_text(code)}"


def _referral_number(value: Any, default: int = 0) -> int:
    try:
        return int(float(value or 0))
    except Exception:
        return default


def _referral_amount(value: Any) -> float:
    try:
        return float(str(value or "0").replace(",", "").replace("₦", "").strip())
    except Exception:
        return 0.0


def _referral_money(value: Any, currency: str = "NGN") -> str:
    amount = _referral_amount(value)
    symbol = "₦" if _clean_text(currency).upper() in {"NGN", "N"} else f"{_clean_text(currency).upper()} "
    return f"{symbol}{amount:,.0f}"


def _referral_profile_row(account_id: str) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    account_id = _clean_text(account_id)
    if not account_id:
        return None, "missing_account_id"

    try:
        resp = (
            supabase.table("referral_profiles")
            .select("*")
            .eq("account_id", account_id)
            .limit(1)
            .execute()
        )
        row = _first(resp)
        if row:
            return row, None
    except Exception as exc:
        return None, f"referral_profiles_select_failed: {type(exc).__name__}"

    return None, None


def _referral_code_available(code: str) -> bool:
    code = _clean_text(code).upper()
    if not code:
        return False

    try:
        resp = (
            supabase.table("referral_profiles")
            .select("*")
            .eq("referral_code", code)
            .eq("is_active", True)
            .limit(1)
            .execute()
        )
        return not bool(_rows(resp))
    except Exception:
        # If the uniqueness check fails, still allow the deterministic fallback.
        # The final insert/table uniqueness policy remains the source of truth.
        return True


def _create_referral_profile_row(account_id: str) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    account_id = _clean_text(account_id)
    if not account_id:
        return None, "missing_account_id"

    base_code = _referral_fallback_code(account_id)
    code = base_code
    if not _referral_code_available(code):
        code = f"{base_code}{uuid.uuid4().hex[:3].upper()}"

    now = _utc_now_iso()
    payload_attempts = [
        {
            "account_id": account_id,
            "referral_code": code,
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        },
        {
            "account_id": account_id,
            "referral_code": code,
            "is_active": True,
            "created_at": now,
        },
        {
            "account_id": account_id,
            "referral_code": code,
        },
    ]

    errors: list[str] = []
    seen: set[str] = set()
    for payload in payload_attempts:
        cleaned = {k: v for k, v in payload.items() if v is not None}
        signature = repr(sorted(cleaned.keys()))
        if signature in seen:
            continue
        seen.add(signature)

        ok, resp, err = _safe_exec(supabase.table("referral_profiles").insert(cleaned))
        if ok:
            row = _first(resp)
            return row or cleaned, None
        errors.append(str(err))

    return None, errors[-1] if errors else "referral_profile_insert_failed"


def _referral_code_link(account_id: str) -> tuple[str, str, Optional[str]]:
    row, err = _referral_profile_row(account_id)
    if not row and not err:
        row, err = _create_referral_profile_row(account_id)

    code = _clean_text((row or {}).get("referral_code")) or _referral_fallback_code(account_id)
    link = _referral_link_for_code(code)
    return code, link, err


def _referral_rows_for_account(account_id: str, limit: int = 50) -> list[dict[str, Any]]:
    try:
        resp = (
            supabase.table("referrals")
            .select("*")
            .eq("referrer_account_id", account_id)
            .order("created_at", desc=True)
            .limit(max(1, min(limit, 100)))
            .execute()
        )
        return _rows(resp)
    except Exception:
        return []


def _referral_rewards_rows_telegram(account_id: str, limit: int = 10) -> list[dict[str, Any]]:
    try:
        resp = (
            supabase.table("referral_rewards")
            .select("*")
            .eq("account_id", account_id)
            .order("created_at", desc=True)
            .limit(max(1, min(limit, 50)))
            .execute()
        )
        return _rows(resp)
    except Exception:
        return []


def _referral_payout_rows_telegram(account_id: str, limit: int = 10) -> list[dict[str, Any]]:
    try:
        resp = (
            supabase.table("referral_payouts")
            .select("*")
            .eq("account_id", account_id)
            .order("created_at", desc=True)
            .limit(max(1, min(limit, 50)))
            .execute()
        )
        return _rows(resp)
    except Exception:
        return []


def _referral_totals_telegram(account_id: str) -> dict[str, Any]:
    referral_rows = _referral_rows_for_account(account_id, limit=100)
    reward_rows = _referral_rewards_rows_telegram(account_id, limit=100)
    payout_rows = _referral_payout_rows_telegram(account_id, limit=100)

    total_referrals = len(referral_rows)
    qualified = 0
    pending_referrals = 0

    for row in referral_rows:
        status = _clean_text(row.get("status")).lower()
        if status in {"qualified", "rewarded", "paid", "approved", "converted", "active"}:
            qualified += 1
        elif status in {"pending", "created", "registered", ""}:
            pending_referrals += 1

    pending_rewards = 0.0
    approved_rewards = 0.0
    paid_rewards = 0.0
    reversed_rewards = 0.0
    currency = "NGN"

    for row in reward_rows:
        currency = _clean_text(row.get("currency") or currency).upper() or "NGN"
        amount = _referral_amount(row.get("reward_amount") or row.get("amount"))
        status = _clean_text(row.get("status")).lower()
        if status in {"paid", "completed", "settled"}:
            paid_rewards += amount
        elif status in {"approved", "available", "ready"}:
            approved_rewards += amount
        elif status in {"reversed", "cancelled", "canceled", "failed"}:
            reversed_rewards += amount
        else:
            pending_rewards += amount

    return {
        "referrals": referral_rows,
        "rewards": reward_rows,
        "payouts": payout_rows,
        "total_referrals": total_referrals,
        "qualified_referrals": qualified,
        "pending_referrals": pending_referrals,
        "pending_rewards": pending_rewards,
        "approved_rewards": approved_rewards,
        "available_balance": approved_rewards,
        "paid_rewards": paid_rewards,
        "reversed_rewards": reversed_rewards,
        "currency": currency,
    }


def _referral_reward_line_telegram(row: dict[str, Any], index: int) -> str:
    reward_type = _clean_text(row.get("reward_type") or "reward").replace("_", " ").title()
    status = _clean_text(row.get("status") or "pending").title()
    currency = _clean_text(row.get("currency") or "NGN").upper()
    amount = _referral_money(row.get("reward_amount") or row.get("amount"), currency)
    created = _date_short(row.get("created_at") or row.get("earned_at"))
    plan = _clean_text(row.get("plan_code"))
    plan_text = f" | Plan: {plan}" if plan else ""
    return f"{index}. {amount} - {status}\n   {reward_type} | {created}{plan_text}"


def _referral_payout_line_telegram(row: dict[str, Any], index: int) -> str:
    status = _clean_text(row.get("status") or "pending").title()
    currency = _clean_text(row.get("currency") or "NGN").upper()
    amount = _referral_money(row.get("amount") or row.get("payout_amount") or row.get("total_amount"), currency)
    created = _date_short(row.get("created_at") or row.get("requested_at"))
    provider = _clean_text(row.get("provider") or "payout").title()
    ref = _clean_text(row.get("provider_reference") or row.get("reference") or row.get("id"))
    ref_text = f"\n   Ref: {_history_excerpt(ref, 80)}" if ref else ""
    return f"{index}. {amount} - {status}\n   {provider} | {created}{ref_text}"


def _send_referral_menu(chat_id: str, account_id: str, action: str) -> None:
    action = _clean_text(action).lower()
    code, link, err = _referral_code_link(account_id)

    if action in {"r1", "referral", "referrals"}:
        body = (
            "🤝 *My Referral Code*\n\n"
            f"Code: {code}\n"
            f"Link: {link}\n\n"
            "Share this code or link with people who need Nigerian tax answers, calculators, reminders, and filing support.\n\n"
            "Reply R2 for only the link, R3 for a ready-to-share invitation, or R4 for referral statistics."
        )
        if err:
            body += "\n\nNote: I used a safe fallback code because the referral profile could not be fully refreshed."
        send_telegram_text(chat_id, _clip_text(body))
        return

    if action == "r2":
        send_telegram_text(
            chat_id,
            "🔗 *My Referral Link*\n\n"
            f"{link}\n\n"
            f"Referral code: {code}\n\n"
            "Reply R3 for a ready-to-share invitation.",
        )
        return

    if action == "r3":
        send_telegram_text(
            chat_id,
            "📣 *Referral Invitation*\n\n"
            "Copy and share this message:\n\n"
            "Hi, I use Naija Tax Guide for Nigerian tax questions, calculators, filing guidance, and reminders.\n\n"
            f"Join with my referral link:\n{link}\n\n"
            f"Referral code: {code}\n\n"
            "After signup, you can use the web app and supported chat channels.",
        )
        return

    if action == "r4":
        totals = _referral_totals_telegram(account_id)
        currency = _clean_text(totals.get("currency") or "NGN").upper()
        body = (
            "📊 *Referral Statistics*\n\n"
            f"Referral code: {code}\n"
            f"Total referrals: {_referral_number(totals.get('total_referrals'))}\n"
            f"Qualified/rewarded referrals: {_referral_number(totals.get('qualified_referrals'))}\n"
            f"Pending referrals: {_referral_number(totals.get('pending_referrals'))}\n\n"
            "Rewards:\n"
            f"Pending: {_referral_money(totals.get('pending_rewards'), currency)}\n"
            f"Approved/available: {_referral_money(totals.get('approved_rewards'), currency)}\n"
            f"Paid: {_referral_money(totals.get('paid_rewards'), currency)}\n"
            f"Reversed: {_referral_money(totals.get('reversed_rewards'), currency)}\n\n"
            "Reply R5 for reward details, R6 for payout status, or 0 for main menu."
        )
        send_telegram_text(chat_id, _clip_text(body))
        return

    if action == "r5":
        rows = _referral_rewards_rows_telegram(account_id, limit=10)
        if not rows:
            send_telegram_text(
                chat_id,
                "🎁 *Referral Rewards*\n\n"
                "No referral reward found yet.\n\n"
                "Share your link with R3. Rewards will appear here after referred users qualify according to the referral policy.\n\n"
                "Reply R1 for your code/link or 0 for main menu.",
            )
            return

        lines = ["🎁 *Referral Rewards*", ""]
        for index, row in enumerate(rows[:5], start=1):
            lines.append(_referral_reward_line_telegram(row, index))
        lines.extend(["", "Reply R4 for statistics, R6 for payout status, or 0 for main menu."])
        send_telegram_text(chat_id, _clip_text("\n".join(lines)))
        return

    if action == "r6":
        totals = _referral_totals_telegram(account_id)
        currency = _clean_text(totals.get("currency") or "NGN").upper()
        rows = _referral_payout_rows_telegram(account_id, limit=10)

        lines = [
            "🏦 *Referral Payout Status*",
            "",
            f"Approved/available rewards: {_referral_money(totals.get('approved_rewards'), currency)}",
            f"Pending rewards: {_referral_money(totals.get('pending_rewards'), currency)}",
            f"Paid rewards: {_referral_money(totals.get('paid_rewards'), currency)}",
            "",
        ]

        if rows:
            lines.append("Recent payouts:")
            for index, row in enumerate(rows[:5], start=1):
                lines.append(_referral_payout_line_telegram(row, index))
        else:
            lines.append("No referral payout request found yet.")

        lines.extend(
            [
                "",
                "Payout requests and payout account setup should still be completed from the secure web Referrals page for now.",
                "",
                "Reply R5 for rewards, R4 for statistics, or 0 for main menu.",
            ]
        )
        send_telegram_text(chat_id, _clip_text("\n".join(lines)))
        return

    send_telegram_text(
        chat_id,
        "🤝 *Referral Centre*\n\n"
        "R1 - My referral code\n"
        "R2 - My referral link\n"
        "R3 - Share referral invitation\n"
        "R4 - Referral statistics\n"
        "R5 - Referral rewards\n"
        "R6 - Payout status\n\n"
        "Reply 0 for main menu.",
    )


def _filing_assistance_menu() -> str:
    return (
        "🗂️ *File Tax / Filing Assistance*\n\n"
        "FT1 - Start filing assistance\n"
        "FT2 - PAYE filing help\n"
        "FT3 - VAT filing help\n"
        "FT4 - CIT filing help\n"
        "FT5 - WHT filing help\n"
        "FT6 - Document checklist\n"
        "FT7 - Request human-assisted filing\n"
        "FT8 - Filing status / latest request\n\n"
        "For calculations, use F1 or C1-C5. For reminders, use D1-D4.\n"
        "Reply with FT1, FT2, FT3, FT4, FT5, FT6, FT7, or FT8.\n"
        "Reply 0 for main menu."
    )


def _filing_document_checklist() -> str:
    return (
        "✅ *Tax Filing Document Checklist*\n\n"
        "Prepare the items that apply to your case:\n\n"
        "General\n"
        "• Taxpayer name / company name\n"
        "• TIN, CAC/RC/BN details where applicable\n"
        "• Contact details and tax office/state/FIRS details\n"
        "• Prior tax filings, assessments, receipts, and notices\n\n"
        "PAYE\n"
        "• Employee list and monthly payroll schedule\n"
        "• Salary, allowances, benefits, pension, NHF, and other deductions\n"
        "• Evidence of PAYE remittance and pension/NHF records\n\n"
        "VAT\n"
        "• Sales invoices and output VAT schedule\n"
        "• Purchase invoices and input VAT support\n"
        "• Bank/payment records and VAT remittance receipts\n\n"
        "CIT\n"
        "• Financial statements / management accounts\n"
        "• Profit computation, expense schedules, and bank statements\n"
        "• Capital allowance, WHT credit notes, and prior-year returns\n\n"
        "WHT\n"
        "• Contract/payment details\n"
        "• WHT rate used and remittance evidence\n"
        "• Credit notes received/issued\n\n"
        "Reply FT7 to request human-assisted filing, or 0 for main menu."
    )


def _filing_help_text(action: str) -> str:
    action = _clean_text(action).lower()
    messages = {
        "ft2": (
            "👥 *PAYE Filing Help*\n\n"
            "PAYE filing usually requires payroll records, employee details, taxable pay, reliefs/deductions, tax deducted, and remittance evidence.\n\n"
            "Useful steps:\n"
            "1. Confirm employee payroll for the month.\n"
            "2. Calculate PAYE correctly.\n"
            "3. Keep remittance proof and employee records.\n"
            "4. File/remit to the relevant State Internal Revenue Service.\n\n"
            "Use C1 for PAYE calculator, FT6 for checklist, or FT7 for human-assisted filing."
        ),
        "ft3": (
            "🧾 *VAT Filing Help*\n\n"
            "VAT filing usually requires sales invoices, output VAT, input VAT support, bank/payment records, and VAT remittance details.\n\n"
            "Useful steps:\n"
            "1. Confirm VATable sales for the period.\n"
            "2. Separate output VAT and allowable input VAT.\n"
            "3. Keep invoice and payment evidence.\n"
            "4. Submit/remit through the correct FIRS process.\n\n"
            "Use C3 for VAT calculator, FT6 for checklist, or FT7 for human-assisted filing."
        ),
        "ft4": (
            "🏢 *CIT Filing Help*\n\n"
            "Company Income Tax filing usually requires financial statements, profit computation, expense schedules, capital allowance details, WHT credit notes, and prior filings.\n\n"
            "Useful steps:\n"
            "1. Confirm accounting year-end.\n"
            "2. Prepare accounts and tax computation.\n"
            "3. Check applicable company size/rate rules.\n"
            "4. Keep supporting documents before submission.\n\n"
            "Use C2 for CIT calculator, FT6 for checklist, or FT7 for human-assisted filing."
        ),
        "ft5": (
            "💼 *WHT Filing Help*\n\n"
            "Withholding Tax filing/remittance usually requires contract/payment details, recipient information, applicable rate, tax deducted, and remittance evidence.\n\n"
            "Useful steps:\n"
            "1. Confirm if the payment is WHT-applicable.\n"
            "2. Apply the correct rate.\n"
            "3. Remit to the relevant authority.\n"
            "4. Keep WHT credit notes/receipts.\n\n"
            "Use C4 for WHT calculator, FT6 for checklist, or FT7 for human-assisted filing."
        ),
    }
    return messages.get(action, _filing_assistance_menu())


def _filing_request_message_from_command(text: Any) -> str:
    raw = _clean_text(text)
    return re.sub(r"^FT7\b[:\-\s]*", "", raw, flags=re.I).strip()


def _filing_subject_from_message(value: Any) -> str:
    text = _filing_request_message_from_command(value)
    if not text:
        return "Human-assisted tax filing request"
    first_sentence = re.split(r"[\n\r.!?]", text, maxsplit=1)[0].strip()
    subject = first_sentence or text
    if not subject.lower().startswith("filing"):
        subject = f"Filing assistance: {subject}"
    return subject[:120]


def _filing_request_priority(text: str) -> str:
    low = _clean_text(text).lower()
    if any(word in low for word in ("urgent", "deadline today", "overdue", "penalty", "audit", "notice")):
        return "high"
    return "normal"


def _create_filing_request_telegram(chat_id: str, account_id: str, text_raw: str) -> None:
    clean_message = _filing_request_message_from_command(text_raw)
    if len(clean_message) < 10:
        send_telegram_text(
            chat_id,
            "🗂️ *Request Human-Assisted Filing*\n\n"
            "Please send FT7 followed by what you need help filing in one clear message.\n\n"
            "Example:\n"
            "FT7 I need help filing VAT for my business for April 2026. I have sales invoices and bank records.\n\n"
            "Reply FT6 for document checklist or CANCEL to stop.",
        )
        return

    ticket_id = f"NTG-FT-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    now = _utc_now_iso()
    subject = _filing_subject_from_message(clean_message)
    preview = " ".join(clean_message.split())[:200]
    payload = {
        "ticket_id": ticket_id,
        "account_id": account_id,
        "category": "filing_assistance",
        "priority": _filing_request_priority(clean_message),
        "subject": subject,
        "message": clean_message,
        "status": "open",
        "issue_type": "filing_assistance",
        "last_message_preview": preview,
        "metadata": {"created_from": "telegram", "request_type": "filing_assistance", "command": "FT7"},
        "created_at": now,
        "updated_at": now,
    }

    result = _support_insert_ticket(payload)
    if not result.get("ok"):
        send_telegram_text(
            chat_id,
            "⚠️ I could not save your filing request right now.\n\n"
            "Please try again shortly or contact support.\n\n"
            "Support email: support@naijataxguides.com",
        )
        return

    send_telegram_text(
        chat_id,
        "✅ *Filing Assistance Request Created*\n\n"
        f"Ticket ID: {ticket_id}\n"
        "Status: Open\n\n"
        f"Request:\n{_clip_text(clean_message, 1200)}\n\n"
        "Next step: prepare your documents using FT6. A support/admin user can review and follow up.\n\n"
        "Reply FT8 to view latest filing request, SUP4 to add more details, or 0 for main menu.",
    )


def _is_filing_ticket_telegram(row: dict[str, Any]) -> bool:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    combined = " ".join(
        _clean_text(x).lower()
        for x in (
            row.get("category"),
            row.get("issue_type"),
            row.get("subject"),
            row.get("message"),
            metadata.get("request_type"),
        )
        if x is not None
    )
    return "filing" in combined or "file tax" in combined or "tax filing" in combined


def _latest_filing_request_text_telegram(account_id: str) -> str:
    rows = _support_rows(account_id, limit=10)
    filing_rows = [row for row in rows if _is_filing_ticket_telegram(row)]

    if not filing_rows:
        return (
            "🗂️ *Filing Request Status*\n\n"
            "No human-assisted filing request was found yet.\n\n"
            "Reply FT7 to create one, FT6 for document checklist, or 0 for main menu."
        )

    ticket = filing_rows[0]
    ticket_id = _support_ref(ticket)
    status = _support_status(ticket).title()
    subject = _clean_text(ticket.get("subject") or "Filing assistance request")
    message = _clean_text(ticket.get("message") or ticket.get("last_message_preview") or "No message preview.")
    created = _date_short(ticket.get("created_at"))
    updated = _date_short(ticket.get("updated_at"))
    admin_note = _support_admin_note(ticket)

    body = (
        "🗂️ *Latest Filing Request*\n\n"
        f"Ticket ID: {ticket_id}\n"
        f"Status: {status}\n"
        f"Created: {created}\n"
        f"Updated: {updated}\n\n"
        f"Subject:\n{_clip_text(subject, 250)}\n\n"
        f"Request:\n{_clip_text(message, 1200)}\n"
    )

    if admin_note:
        body += f"\nAdmin note:\n{_clip_text(admin_note, 800)}\n"

    body += "\nReply SUP4 to add more details, SUP5 to close it, FT6 for documents, or 0 for main menu."
    return _clip_text(body)


def _send_filing_assistance(chat_id: str, account_id: str, action: str, text_raw: str = "") -> None:
    action = _clean_text(action).lower()

    if action == "ft1":
        send_telegram_text(chat_id, _filing_assistance_menu())
        return
    if action in {"ft2", "ft3", "ft4", "ft5"}:
        send_telegram_text(chat_id, _clip_text(_filing_help_text(action)))
        return
    if action == "ft6":
        send_telegram_text(chat_id, _clip_text(_filing_document_checklist()))
        return
    if action == "ft7":
        _create_filing_request_telegram(chat_id, account_id, text_raw)
        return
    if action == "ft8":
        send_telegram_text(chat_id, _latest_filing_request_text_telegram(account_id))
        return

    send_telegram_text(chat_id, _filing_assistance_menu())


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
            _send_support_tickets(chat_id, account_id, mode="latest")
        elif cmd == "SUP3":
            _send_support_tickets(chat_id, account_id, mode="open")
        elif cmd == "SUP4":
            _append_support_reply(chat_id, account_id, text_raw)
        elif cmd == "SUP5":
            _close_support_ticket(chat_id, account_id, text_raw)
        elif cmd == "SUP6":
            _send_support_email(chat_id)
        return True

    if cmd.startswith("R"):
        _send_referral_menu(chat_id, account_id, cmd.lower())
        return True

    if cmd.startswith("FT"):
        _send_filing_assistance(chat_id, account_id, cmd.lower(), text_raw)
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

    if cmd.startswith("F"):
        if cmd == "F1":
            _send_calculator_menu(chat_id)
        elif cmd in {"F2", "F3", "F4", "F5"}:
            # F2-F5 mirror the WhatsApp tool menu but reuse Telegram's filing help handlers.
            _send_filing_assistance(chat_id, account_id, f"ft{cmd[1]}", text_raw)
        elif cmd == "F6":
            send_telegram_text(chat_id, _telegram_deadline_usage_text(account_id))
        elif cmd == "F7":
            _send_filing_assistance(chat_id, account_id, "ft6", text_raw)
        elif cmd == "F8":
            _send_main_menu(chat_id, linked=linked)
        else:
            _send_tax_menu(chat_id)
        return True

    if cmd.startswith("C"):
        if cmd in CALCULATOR_COMMAND_TO_TYPE:
            _start_calculator_flow(chat_id, cmd, text_raw=text_raw)
        elif cmd == "C6":
            _handle_quiz_command_telegram(chat_id, account_id, tg_user_id, "Q1")
        elif cmd == "C7":
            send_telegram_text(chat_id, _telegram_deadline_usage_text(account_id))
        elif cmd == "C8":
            _send_tax_menu(chat_id)
        else:
            _send_calculator_menu(chat_id)
        return True

    if cmd.startswith("Q"):
        _handle_quiz_command_telegram(chat_id, account_id, tg_user_id, text_raw)
        return True

    if cmd.startswith("D"):
        _handle_deadline_command_telegram(chat_id, account_id, tg_user_id, text_raw)
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
        "F2 - PAYE filing guide\n"
        "F3 - VAT filing guide\n"
        "F4 - CIT filing guide\n"
        "F5 - WHT guide\n"
        "F6 - Tax deadlines/calendar\n"
        "F7 - Filing checklist\n"
        "F8 - Back to main menu\n"
        "C1 - PAYE calculator\n"
        "C2 - Company Income Tax calculator\n"
        "C3 - VAT calculator\n"
        "C4 - Withholding Tax calculator\n"
        "C5 - Salary/net pay comparison\n"
        "C6 or Q1 - Tax quiz\n"
        "C7 - Tax calendar/deadlines\n"
        "C8 - Back to Tax Tools\n"
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
    reuse_msg = (
        _recent_checkout_reuse_message(account_id, tg_user_id=tg_user_id, package_code=package_code)
        if package_code
        else None
    )
    if reuse_msg:
        send_telegram_text(chat_id, reuse_msg)
        return True

    result = create_credit_payment(account_id, package_num, "telegram", tg_user_id)
    if result.get("ok") and package_code:
        _record_telegram_checkout_lock(
            tg_user_id=tg_user_id,
            account_id=account_id,
            kind="topup",
            code=package_code,
            result=result,
        )
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
    rows = _combined_credit_activity_rows(account_id, mode="ai", limit=5)
    balance = get_credit_balance(account_id)

    if not rows:
        bal = _credit_balance_value(balance) if isinstance(balance, dict) else "Not shown"
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
        amount = row.get("credits_delta")
        if amount is None or amount == "":
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
        msg += f"Usage Credits: {_credit_balance_value(balance)}\n"

    msg += "\nReply ACC2 for link/unlink help or 0 for main menu."
    send_telegram_text(chat_id, msg)



# ---------------------------------------------------------------------------
# Batch 28B - Telegram calculator parity
# ---------------------------------------------------------------------------

CALCULATOR_LABELS = {
    "paye": "PAYE",
    "cit": "Company Income Tax",
    "vat": "VAT",
    "wht": "Withholding Tax",
    "salary": "Salary / Net Pay",
}

CALCULATOR_COMMAND_TO_TYPE = {
    "C1": "paye",
    "C2": "cit",
    "C3": "vat",
    "C4": "wht",
    "C5": "salary",
}


def _format_money_amount(value: Any) -> str:
    try:
        return f"₦{float(value or 0):,.2f}"
    except Exception:
        return "₦0.00"


def _parse_percent(text: str) -> float:
    return float(_clean_text(text).replace("%", "").replace(",", "").strip())


def _extract_numbers(text: str) -> list[float]:
    values: list[float] = []
    for raw in re.findall(r"₦?\s*-?\d+(?:,\d{3})*(?:\.\d+)?%?", _clean_text(text)):
        cleaned = raw.replace("₦", "").replace(",", "").replace("%", "").strip()
        if not cleaned:
            continue
        try:
            values.append(float(cleaned))
        except Exception:
            continue
    return values


def _paye_fallback(inputs: dict[str, Any]) -> dict[str, Any]:
    monthly_gross = float(inputs.get("monthly_gross_income") or inputs.get("monthly_gross") or 0)
    monthly_pension = float(inputs.get("pension_contribution") or inputs.get("pension") or 0)
    monthly_nhf = float(inputs.get("nhf") or inputs.get("nhf_contribution") or 0)

    annual_gross = monthly_gross * 12
    annual_pension = monthly_pension * 12
    annual_nhf = monthly_nhf * 12

    consolidated_relief = max(200000.0, annual_gross * 0.01) + (annual_gross * 0.20)
    chargeable_income = max(0.0, annual_gross - consolidated_relief - annual_pension - annual_nhf)

    bands = [
        (300000.0, 0.07),
        (300000.0, 0.11),
        (500000.0, 0.15),
        (500000.0, 0.19),
        (1600000.0, 0.21),
        (float("inf"), 0.24),
    ]

    remaining = chargeable_income
    annual_tax = 0.0
    for band_amount, rate in bands:
        if remaining <= 0:
            break
        taxable = min(remaining, band_amount)
        annual_tax += taxable * rate
        remaining -= taxable

    return {
        "monthly_gross_income": monthly_gross,
        "annual_gross_income": annual_gross,
        "annual_pension": annual_pension,
        "annual_nhf": annual_nhf,
        "consolidated_relief": consolidated_relief,
        "chargeable_income": chargeable_income,
        "annual_tax_payable": annual_tax,
        "monthly_tax_payable": annual_tax / 12,
        "net_monthly_pay": max(0.0, monthly_gross - monthly_pension - monthly_nhf - (annual_tax / 12)),
    }


def _vat_fallback(inputs: dict[str, Any]) -> dict[str, Any]:
    taxable_supplies = float(inputs.get("taxable_supplies") or 0)
    input_vat = float(inputs.get("input_vat") or 0)
    output_vat = taxable_supplies * 0.075
    vat_payable = max(0.0, output_vat - input_vat)
    return {
        "taxable_supplies": taxable_supplies,
        "input_vat": input_vat,
        "output_vat": output_vat,
        "vat_payable": vat_payable,
        "rate": 7.5,
    }


def _cit_fallback(inputs: dict[str, Any]) -> dict[str, Any]:
    taxable_profit = float(inputs.get("taxable_profit") or inputs.get("assessable_profit") or 0)
    annual_turnover = float(inputs.get("annual_turnover") or 0)

    if annual_turnover <= 25000000:
        rate = 0.0
        company_size = "small company"
    elif annual_turnover <= 100000000:
        rate = 20.0
        company_size = "medium company"
    else:
        rate = 30.0
        company_size = "large company"

    cit_payable = max(0.0, taxable_profit * (rate / 100.0))
    return {
        "taxable_profit": taxable_profit,
        "assessable_profit": taxable_profit,
        "annual_turnover": annual_turnover,
        "company_size": company_size,
        "applicable_rate": rate,
        "cit_payable": cit_payable,
    }


def _wht_fallback(inputs: dict[str, Any]) -> dict[str, Any]:
    amount = float(inputs.get("payment_amount") or 0)
    rate = float(inputs.get("wht_rate") or 0)
    wht_payable = max(0.0, amount * (rate / 100.0))
    net_payment = max(0.0, amount - wht_payable)
    return {
        "payment_amount": amount,
        "wht_rate": rate,
        "wht_payable": wht_payable,
        "net_payment": net_payment,
    }


def _calculator_result(calculator_type: str, inputs: dict[str, Any]) -> dict[str, Any]:
    """
    Use the backend tax calculator where it is compatible, but keep safe
    local fallbacks so Telegram calculator does not fail because of one
    service signature mismatch.
    """
    if calculator_type in {"paye", "salary"}:
        fallback = _paye_fallback(inputs)
        try:
            service_result = calculate_tax("paye", inputs)
            if isinstance(service_result, dict):
                fallback.update({k: v for k, v in service_result.items() if v is not None})
        except Exception:
            logging.exception("Telegram PAYE calculator service fallback used")
        return fallback

    if calculator_type == "vat":
        fallback = _vat_fallback(inputs)
        try:
            service_result = calculate_tax("vat", inputs)
            if isinstance(service_result, dict):
                fallback.update({k: v for k, v in service_result.items() if v is not None})
        except Exception:
            logging.exception("Telegram VAT calculator service fallback used")
        return fallback

    if calculator_type == "cit":
        # Existing service expects filing-style fields in some versions. Use a
        # stable Telegram fallback to avoid accidental wrong inputs.
        return _cit_fallback(inputs)

    if calculator_type == "wht":
        return _wht_fallback(inputs)

    return {}


def _calculator_disclaimer() -> str:
    return (
        "\n\nNote: This is an estimate for guidance only. Confirm taxpayer-specific "
        "facts, current law, exemptions, and filing position before submission."
    )


def _send_calculator_menu(chat_id: str) -> None:
    msg = (
        "🧮 *Tax Calculator Menu*\n\n"
        "Reply with:\n"
        "C1 - PAYE calculator\n"
        "C2 - Company Income Tax calculator\n"
        "C3 - VAT calculator\n"
        "C4 - Withholding Tax calculator\n"
        "C5 - Salary / net pay estimate\n"
        "C6 - Tax quiz\n"
        "C7 - Tax calendar/deadlines\n"
        "C8 - Back to Tax Tools\n\n"
        "Fast examples:\n"
        "C1 750000 0 0\n"
        "C2 12000000 80000000\n"
        "C3 5000000 120000\n"
        "C4 1000000 5\n"
        "C5 750000 0 0\n\n"
        "These calculator commands do not use AI credits.\n"
        "Reply 0 for main menu."
    )
    send_telegram_text(chat_id, msg)


def _calculator_prompt(calculator_type: str, step: int) -> str:
    if calculator_type == "paye":
        prompts = {
            1: "🧮 *PAYE Calculator*\n\nStep 1 of 3: Enter monthly gross salary.\nExample: 750000",
            2: "Step 2 of 3: Enter monthly pension contribution, or 0 if none.",
            3: "Step 3 of 3: Enter monthly NHF contribution, or 0 if none.",
        }
        return prompts.get(step, "Enter the amount.")

    if calculator_type == "salary":
        prompts = {
            1: "🧮 *Salary / Net Pay Estimate*\n\nStep 1 of 3: Enter monthly gross salary.\nExample: 750000",
            2: "Step 2 of 3: Enter monthly pension contribution, or 0 if none.",
            3: "Step 3 of 3: Enter monthly NHF contribution, or 0 if none.",
        }
        return prompts.get(step, "Enter the amount.")

    if calculator_type == "vat":
        prompts = {
            1: "🧮 *VAT Calculator*\n\nStep 1 of 2: Enter total taxable supplies/sales for the period.\nExample: 5000000",
            2: "Step 2 of 2: Enter input VAT already paid/claimable, or 0 if none.",
        }
        return prompts.get(step, "Enter the amount.")

    if calculator_type == "cit":
        prompts = {
            1: "🧮 *Company Income Tax Calculator*\n\nStep 1 of 2: Enter annual taxable profit.\nExample: 12000000",
            2: "Step 2 of 2: Enter annual company turnover.\nExample: 80000000",
        }
        return prompts.get(step, "Enter the amount.")

    if calculator_type == "wht":
        prompts = {
            1: "🧮 *Withholding Tax Calculator*\n\nStep 1 of 2: Enter payment/contract amount.\nExample: 1000000",
            2: "Step 2 of 2: Enter WHT rate percentage.\nExample: 5",
        }
        return prompts.get(step, "Enter the amount.")

    return "Enter the amount."


def _calculator_step_count(calculator_type: str) -> int:
    return {
        "paye": 3,
        "salary": 3,
        "vat": 2,
        "cit": 2,
        "wht": 2,
    }.get(calculator_type, 0)


def _calculator_store_value(inputs: dict[str, Any], calculator_type: str, step: int, value: float) -> dict[str, Any]:
    if calculator_type in {"paye", "salary"}:
        if step == 1:
            inputs["monthly_gross_income"] = value
        elif step == 2:
            inputs["pension_contribution"] = value
        elif step == 3:
            inputs["nhf"] = value
        return inputs

    if calculator_type == "vat":
        if step == 1:
            inputs["taxable_supplies"] = value
        elif step == 2:
            inputs["input_vat"] = value
        return inputs

    if calculator_type == "cit":
        if step == 1:
            inputs["taxable_profit"] = value
        elif step == 2:
            inputs["annual_turnover"] = value
        return inputs

    if calculator_type == "wht":
        if step == 1:
            inputs["payment_amount"] = value
        elif step == 2:
            inputs["wht_rate"] = value
        return inputs

    return inputs


def _calculator_result_text(calculator_type: str, inputs: dict[str, Any]) -> str:
    calc = _calculator_result(calculator_type, inputs)
    label = CALCULATOR_LABELS.get(calculator_type, "Tax")

    if calculator_type == "paye":
        return (
            f"✅ *{label} Estimate*\n\n"
            f"Monthly gross salary: {_format_money_amount(inputs.get('monthly_gross_income'))}\n"
            f"Monthly pension: {_format_money_amount(inputs.get('pension_contribution'))}\n"
            f"Monthly NHF: {_format_money_amount(inputs.get('nhf'))}\n\n"
            f"Annual gross income: {_format_money_amount(calc.get('annual_gross_income'))}\n"
            f"Consolidated relief: {_format_money_amount(calc.get('consolidated_relief'))}\n"
            f"Chargeable income: {_format_money_amount(calc.get('chargeable_income'))}\n"
            f"Annual PAYE: {_format_money_amount(calc.get('annual_tax_payable'))}\n"
            f"*Monthly PAYE deduction: {_format_money_amount(calc.get('monthly_tax_payable'))}*"
            f"{_calculator_disclaimer()}\n\n"
            "Reply C1 to calculate again, F1 for calculator menu, or 0 for main menu."
        )

    if calculator_type == "salary":
        return (
            f"✅ *{label} Estimate*\n\n"
            f"Monthly gross salary: {_format_money_amount(inputs.get('monthly_gross_income'))}\n"
            f"Monthly pension: {_format_money_amount(inputs.get('pension_contribution'))}\n"
            f"Monthly NHF: {_format_money_amount(inputs.get('nhf'))}\n\n"
            f"Estimated monthly PAYE: {_format_money_amount(calc.get('monthly_tax_payable'))}\n"
            f"*Estimated net monthly pay: {_format_money_amount(calc.get('net_monthly_pay'))}*"
            f"{_calculator_disclaimer()}\n\n"
            "Reply C5 to calculate again, F1 for calculator menu, or 0 for main menu."
        )

    if calculator_type == "vat":
        return (
            f"✅ *{label} Estimate*\n\n"
            f"Taxable supplies: {_format_money_amount(inputs.get('taxable_supplies'))}\n"
            f"VAT rate: {calc.get('rate', 7.5)}%\n"
            f"Output VAT: {_format_money_amount(calc.get('output_vat'))}\n"
            f"Input VAT: {_format_money_amount(calc.get('input_vat'))}\n"
            f"*VAT payable: {_format_money_amount(calc.get('vat_payable'))}*"
            f"{_calculator_disclaimer()}\n\n"
            "Reply C3 to calculate again, F1 for calculator menu, or 0 for main menu."
        )

    if calculator_type == "cit":
        return (
            f"✅ *{label} Estimate*\n\n"
            f"Annual taxable profit: {_format_money_amount(calc.get('taxable_profit'))}\n"
            f"Annual turnover: {_format_money_amount(calc.get('annual_turnover'))}\n"
            f"Company class: {str(calc.get('company_size') or 'N/A').title()}\n"
            f"Applicable rate: {calc.get('applicable_rate', 0)}%\n"
            f"*Estimated CIT payable: {_format_money_amount(calc.get('cit_payable'))}*"
            f"{_calculator_disclaimer()}\n\n"
            "Reply C2 to calculate again, F1 for calculator menu, or 0 for main menu."
        )

    if calculator_type == "wht":
        return (
            f"✅ *{label} Estimate*\n\n"
            f"Payment amount: {_format_money_amount(calc.get('payment_amount'))}\n"
            f"WHT rate: {calc.get('wht_rate', 0)}%\n"
            f"*WHT to deduct/remit: {_format_money_amount(calc.get('wht_payable'))}*\n"
            f"Net payable after WHT: {_format_money_amount(calc.get('net_payment'))}"
            f"{_calculator_disclaimer()}\n\n"
            "Reply C4 to calculate again, F1 for calculator menu, or 0 for main menu."
        )

    return "I could not calculate that. Reply F1 for calculator menu."


def _run_direct_calculator(chat_id: str, calculator_type: str, numbers: list[float]) -> bool:
    inputs: dict[str, Any] = {}

    if calculator_type in {"paye", "salary"} and len(numbers) >= 1:
        inputs["monthly_gross_income"] = numbers[0]
        inputs["pension_contribution"] = numbers[1] if len(numbers) >= 2 else 0
        inputs["nhf"] = numbers[2] if len(numbers) >= 3 else 0
        send_telegram_text(chat_id, _calculator_result_text(calculator_type, inputs))
        return True

    if calculator_type == "vat" and len(numbers) >= 1:
        inputs["taxable_supplies"] = numbers[0]
        inputs["input_vat"] = numbers[1] if len(numbers) >= 2 else 0
        send_telegram_text(chat_id, _calculator_result_text(calculator_type, inputs))
        return True

    if calculator_type == "cit" and len(numbers) >= 2:
        inputs["taxable_profit"] = numbers[0]
        inputs["annual_turnover"] = numbers[1]
        send_telegram_text(chat_id, _calculator_result_text(calculator_type, inputs))
        return True

    if calculator_type == "wht" and len(numbers) >= 2:
        inputs["payment_amount"] = numbers[0]
        inputs["wht_rate"] = numbers[1]
        send_telegram_text(chat_id, _calculator_result_text(calculator_type, inputs))
        return True

    return False


def _start_calculator_flow(chat_id: str, cmd: str, text_raw: str = "") -> bool:
    cmd = _clean_text(cmd).upper()
    calculator_type = CALCULATOR_COMMAND_TO_TYPE.get(cmd)

    if not calculator_type:
        _send_calculator_menu(chat_id)
        return True

    # Fast direct calculation: e.g. C1 750000 0 0, C3 5000000 120000.
    numbers = _extract_numbers(re.sub(rf"^{re.escape(cmd)}\b", "", _clean_text(text_raw), flags=re.I).strip())
    if numbers and _run_direct_calculator(chat_id, calculator_type, numbers):
        user_states.pop(chat_id, None)
        return True

    user_states[chat_id] = {
        "calculator_type": calculator_type,
        "step": 1,
        "inputs": {},
    }
    send_telegram_text(chat_id, _calculator_prompt(calculator_type, 1))
    return True


def _handle_calculator_step(chat_id: str, user_state: dict[str, Any], text: str) -> bool:
    calculator_type = _clean_text(user_state.get("calculator_type"))
    if not calculator_type:
        return False

    step = int(user_state.get("step") or 1)
    inputs = user_state.get("inputs") or {}
    if not isinstance(inputs, dict):
        inputs = {}

    try:
        if calculator_type == "wht" and step == 2:
            amount = _parse_percent(text)
        else:
            amount = _parse_amount(text)
    except Exception:
        send_telegram_text(chat_id, "❌ Please enter a valid number. Example: 750000")
        return True

    inputs = _calculator_store_value(inputs, calculator_type, step, amount)
    total_steps = _calculator_step_count(calculator_type)

    if step < total_steps:
        next_step = step + 1
        user_states[chat_id] = {
            "calculator_type": calculator_type,
            "step": next_step,
            "inputs": inputs,
        }
        send_telegram_text(chat_id, f"✅ Received: {_format_money_amount(amount) if not (calculator_type == 'wht' and step == 2) else str(amount) + '%'}\n\n{_calculator_prompt(calculator_type, next_step)}")
        return True

    user_states.pop(chat_id, None)
    send_telegram_text(chat_id, _calculator_result_text(calculator_type, inputs))
    return True




# ---------------------------------------------------------------------------
# Batch 28C - Telegram deadline/reminder parity
# ---------------------------------------------------------------------------

DEADLINE_TAX_TYPES = {"PAYE", "VAT", "CIT", "WHT"}

# Batch 28J: cache tax_deadlines columns discovered from SELECT * so D1/D4
# can send a schema-safe payload first instead of triggering avoidable
# PostgREST 400 responses before falling back.
_TELEGRAM_TAX_DEADLINE_COLUMN_CACHE: Optional[set[str]] = None


def _telegram_deadline_today() -> Any:
    return datetime.now(timezone.utc).date()


def _parse_deadline_date(value: Any) -> Optional[Any]:
    try:
        return datetime.strptime(_clean_text(value), "%Y-%m-%d").date()
    except Exception:
        return None


def _valid_deadline_time(value: Any) -> str:
    raw = _clean_text(value or "09:00")
    match = re.match(r"^(\d{1,2}):(\d{2})$", raw)
    if not match:
        return "09:00"

    hour = int(match.group(1))
    minute = int(match.group(2))
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return "09:00"
    return f"{hour:02d}:{minute:02d}"


def _valid_deadline_mode(value: Any) -> str:
    raw = _clean_text(value or "telegram").lower().replace(" ", "")
    allowed = {
        "telegram",
        "email",
        "sms",
        "telegram,email",
        "telegram,sms",
        "email,sms",
        "telegram,email,sms",
        "whatsapp",
        "whatsapp,email",
        "whatsapp,sms",
        "whatsapp,email,sms",
    }
    return raw if raw in allowed else "telegram"


def _deadline_allowed_for_account_telegram(account_id: str) -> bool:
    try:
        return bool(has_active_subscription(account_id))
    except Exception:
        row = _subscription_row(account_id)
        if not row:
            return False
        status = _clean_text(row.get("status")).lower()
        plan_code = _clean_text(row.get("plan_code")).lower()
        if status in {"inactive", "expired", "cancelled", "canceled", "disabled"}:
            return False
        return any(x in plan_code for x in ("starter", "professional", "business"))


def _telegram_deadline_usage_text(account_id: str) -> str:
    if not _deadline_allowed_for_account_telegram(account_id):
        return (
            "📅 *Tax Deadline Reminders*\n\n"
            "Free users can view the general tax calendar. Custom reminder creation is available on paid plans.\n\n"
            "D1 - Create reminder 🔔 (paid)\n"
            "D2 - View reminders 📋\n"
            "D3 - Delete reminder 🗑️ (paid)\n"
            "D4 - Update reminder ⚙️ (paid)\n\n"
            "General guide:\n"
            "• PAYE: usually monthly, commonly by the 10th of the following month.\n"
            "• VAT: usually monthly, commonly by the 21st of the following month.\n"
            "• CIT: generally due within 6 months after company year-end.\n\n"
            "Reply 4 to view plans or 0 for main menu."
        )

    return (
        "📅 *Tax Deadline Reminders*\n\n"
        "Use these commands:\n"
        "D1 PAYE 2026-06-10 3 - create PAYE reminder 3 days before\n"
        "D1 VAT 2026-06-21 7 09:00 telegram - create VAT reminder\n"
        "D2 - view my reminders\n"
        "D3 1 - delete reminder number 1 from D2 list\n"
        "D4 1 2 08:30 telegram - update reminder number 1 to 2 days before\n\n"
        "Supported types: PAYE, VAT, CIT, WHT.\n"
        "Reminder date must not be in the past.\n"
        "These commands do not use AI credits."
    )


def _parse_deadline_create_telegram(text: str) -> Optional[dict[str, Any]]:
    raw = _clean_text(text).upper()

    match = re.search(
        r"\bD1\s+(PAYE|VAT|CIT|WHT)\s+(\d{4}-\d{2}-\d{2})(?:\s+(\d{1,3}))?(?:\s+(\d{1,2}:\d{2}))?(?:\s+([A-Z,]+))?",
        raw,
        flags=re.I,
    )
    if not match:
        return None

    tax_type = match.group(1).upper()
    due_date = match.group(2)
    try:
        reminder_days = int(match.group(3) or 7)
    except Exception:
        reminder_days = 7

    reminder_days = max(0, min(365, reminder_days))
    reminder_time = _valid_deadline_time(match.group(4) or "09:00")
    reminder_mode = _valid_deadline_mode(match.group(5) or "telegram")

    if not _parse_deadline_date(due_date):
        return None

    return {
        "tax_type": tax_type,
        "due_date": due_date,
        "reminder_days_before": reminder_days,
        "reminder_time": reminder_time,
        "timezone": "Africa/Lagos",
        "reminder_mode": reminder_mode,
        "source": "telegram",
    }


def _deadline_validation_telegram(due_date_text: Any, reminder_days: Any) -> dict[str, Any]:
    today = _telegram_deadline_today()
    due_date = _parse_deadline_date(due_date_text)

    try:
        days = int(reminder_days)
    except Exception:
        days = 7

    days = max(0, min(365, days))

    if not due_date:
        return {
            "ok": False,
            "reason": "invalid_due_date",
            "max_days": 0,
            "message": "The due date is invalid. Use YYYY-MM-DD, for example D1 PAYE 2026-06-10 3.",
        }

    days_until_due = (due_date - today).days
    reminder_date = due_date - timedelta(days=days)

    if days_until_due < 0:
        return {
            "ok": False,
            "reason": "due_date_passed",
            "today": today.isoformat(),
            "due_date": due_date.isoformat(),
            "reminder_date": reminder_date.isoformat(),
            "max_days": 0,
            "message": f"The due date {due_date.isoformat()} has already passed. Please choose a future due date.",
        }

    if reminder_date < today:
        max_days = max(0, days_until_due)
        return {
            "ok": False,
            "reason": "reminder_date_passed",
            "today": today.isoformat(),
            "due_date": due_date.isoformat(),
            "reminder_date": reminder_date.isoformat(),
            "max_days": max_days,
            "message": (
                f"That reminder is no longer possible.\n\n"
                f"Today: {today.isoformat()}\n"
                f"Due date: {due_date.isoformat()}\n"
                f"Requested reminder: {days} day(s) before\n"
                f"Reminder date would be: {reminder_date.isoformat()}\n\n"
                f"Use {max_days} day(s) before or choose a later due date."
            ),
        }

    return {
        "ok": True,
        "reason": "valid",
        "today": today.isoformat(),
        "due_date": due_date.isoformat(),
        "reminder_date": reminder_date.isoformat(),
        "max_days": max(0, days_until_due),
        "days": days,
    }


def _deadline_computed_status_telegram(item: dict[str, Any]) -> str:
    enabled = bool(item.get("enabled", True))
    validation = _deadline_validation_telegram(item.get("due_date"), item.get("reminder_days_before", 7))
    if not validation.get("ok"):
        return "inactive"
    return "active" if enabled else "inactive"


def _deadline_mode_for_display_telegram(item: dict[str, Any]) -> str:
    """
    Resolve the reminder delivery mode for Telegram display.

    Batch 28J reason:
    Some tax_deadlines schemas default channel/reminder_mode to whatsapp.
    When a Telegram reminder is created through a schema-safe fallback, older
    code could display the new reminder as "via whatsapp". This helper now
    prefers explicit Telegram fields when present and defaults empty/minimal
    Telegram rows to telegram instead of inheriting a whatsapp default.
    """
    reminder_mode = _clean_text(item.get("reminder_mode")).lower()
    channel = _clean_text(item.get("channel")).lower()
    source = _clean_text(item.get("source")).lower()

    for value in (reminder_mode, channel, source):
        if value in {"telegram", "tg"} or value.startswith("telegram"):
            return "telegram"

    if reminder_mode:
        return reminder_mode
    if channel:
        return channel

    return "telegram"


def _deadline_display_line_telegram(item: dict[str, Any], index: int) -> str:
    tax_type = _clean_text(item.get("tax_type") or item.get("title") or "Tax").upper()
    due = _clean_text(item.get("due_date") or "No date")
    try:
        days_text = f"{int(item.get('reminder_days_before', 7))} days before"
    except Exception:
        days_text = "7 days before"
    status = _deadline_computed_status_telegram(item)
    time_text = _clean_text(item.get("reminder_time") or "09:00")
    mode_text = _deadline_mode_for_display_telegram(item)
    return f"{index}. {tax_type} - due {due} - reminder {days_text} - {status} - {time_text} via {mode_text}"

def _telegram_deadline_rows(account_id: str, limit: int = 10) -> list[dict[str, Any]]:
    try:
        resp = (
            supabase.table("tax_deadlines")
            .select("*")
            .eq("account_id", account_id)
            .order("created_at", desc=True)
            .limit(max(1, min(limit, 20)))
            .execute()
        )
        return _rows(resp)
    except Exception:
        try:
            resp = (
                supabase.table("tax_deadlines")
                .select("*")
                .eq("account_id", account_id)
                .limit(max(1, min(limit, 20)))
                .execute()
            )
            return _rows(resp)
        except Exception:
            logging.exception("Telegram tax_deadlines list failed")
            return []


def _telegram_deadline_by_index(account_id: str, index: int) -> Optional[dict[str, Any]]:
    rows = _telegram_deadline_rows(account_id, limit=10)
    if index < 1 or index > len(rows):
        return None
    return rows[index - 1]


def _telegram_deadline_column_names_telegram(account_id: str = "") -> set[str]:
    """
    Discover currently available tax_deadlines columns without relying on a
    failing INSERT attempt.

    Supabase/PostgREST returns real column keys with select("*"). This lets the
    Telegram route keep optional fields such as reminder_time/reminder_mode/
    channel only when the deployed table actually exposes them.
    """
    global _TELEGRAM_TAX_DEADLINE_COLUMN_CACHE

    if _TELEGRAM_TAX_DEADLINE_COLUMN_CACHE is not None:
        return set(_TELEGRAM_TAX_DEADLINE_COLUMN_CACHE)

    # These columns were proven by the successful fallback insert in Batch 28I.
    discovered: set[str] = {
        "user_id",
        "account_id",
        "tax_type",
        "due_date",
        "reminder_days_before",
        "enabled",
        "updated_at",
    }

    try:
        q = supabase.table("tax_deadlines").select("*")
        if _clean_text(account_id):
            q = q.eq("account_id", account_id)
        resp = q.limit(1).execute()
        rows = _rows(resp)
        if rows:
            discovered.update(str(k) for k in rows[0].keys())
    except Exception:
        logging.exception("Telegram tax_deadlines column discovery failed")

    # If the table has channel/reminder_mode columns and there are existing rows,
    # select("*") above will expose them. If there are no rows, we avoid guessing
    # optional columns so the first INSERT remains clean.
    _TELEGRAM_TAX_DEADLINE_COLUMN_CACHE = set(discovered)
    return set(discovered)


def _deadline_payload_filtered_telegram(payload: dict[str, Any], columns: set[str], *, include_updated_at: bool = True) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}

    for key, value in payload.items():
        if value is None:
            continue
        if key == "updated_at" and not include_updated_at:
            continue
        if key in columns:
            cleaned[key] = value

    return cleaned


def _deadline_payload_core_telegram(payload: dict[str, Any], columns: Optional[set[str]] = None, *, include_updated_at: bool = True) -> dict[str, Any]:
    allowed = {
        "user_id",
        "account_id",
        "tax_type",
        "due_date",
        "reminder_days_before",
        "enabled",
    }
    if include_updated_at:
        allowed.add("updated_at")

    cleaned = {k: v for k, v in payload.items() if k in allowed and v is not None}
    if columns:
        cleaned = {k: v for k, v in cleaned.items() if k in columns}
    return cleaned


def _telegram_deadline_payload_for_table(payload: dict[str, Any], *, for_update: bool = False) -> dict[str, Any]:
    """
    Batch 30D:
    Keep only real public.tax_deadlines columns.

    Confirmed production table:
    - public.tax_deadlines has reminder_mode/reminder_time/timezone.
    - public.tax_deadlines does NOT have channel/source.

    This prevents a false local display such as "via telegram" when the database
    actually kept reminder_mode as whatsapp because a fallback payload removed
    the mode/time fields.
    """
    insert_allowed = {
        "id",
        "user_id",
        "account_id",
        "tax_type",
        "due_date",
        "reminder_days_before",
        "enabled",
        "created_at",
        "updated_at",
        "last_reminder_sent_at",
        "reminder_time",
        "timezone",
        "reminder_mode",
        "reminder_email",
        "reminder_phone",
        "reminder_last_error",
    }

    update_allowed = {
        "tax_type",
        "due_date",
        "reminder_days_before",
        "enabled",
        "updated_at",
        "last_reminder_sent_at",
        "reminder_time",
        "timezone",
        "reminder_mode",
        "reminder_email",
        "reminder_phone",
        "reminder_last_error",
    }

    allowed = update_allowed if for_update else insert_allowed
    return {k: v for k, v in payload.items() if k in allowed and v is not None}


def _telegram_deadline_get_by_id(deadline_id: str) -> Optional[dict[str, Any]]:
    deadline_id = _clean_text(deadline_id)
    if not deadline_id:
        return None

    try:
        resp = (
            supabase.table("tax_deadlines")
            .select("*")
            .eq("id", deadline_id)
            .limit(1)
            .execute()
        )
        return _first(resp)
    except Exception:
        logging.exception("Telegram deadline re-read failed")
        return None


def _telegram_deadline_insert(payload: dict[str, Any]) -> dict[str, Any]:
    safe_payload = _telegram_deadline_payload_for_table(payload, for_update=False)

    attempts: list[dict[str, Any]] = [
        dict(safe_payload),

        # Remove contact/error optional fields only. Keep reminder_mode/reminder_time.
        {
            k: v
            for k, v in safe_payload.items()
            if k not in {"reminder_email", "reminder_phone", "reminder_last_error", "last_reminder_sent_at"}
        },

        # Remove updated_at only if a legacy schema ever complains. Keep reminder_mode/reminder_time.
        {
            k: v
            for k, v in safe_payload.items()
            if k not in {"updated_at", "reminder_email", "reminder_phone", "reminder_last_error", "last_reminder_sent_at"}
        },

        # Last-resort base payload.
        {
            "user_id": safe_payload.get("account_id") or safe_payload.get("user_id"),
            "account_id": safe_payload.get("account_id") or safe_payload.get("user_id"),
            "tax_type": safe_payload.get("tax_type"),
            "due_date": safe_payload.get("due_date"),
            "reminder_days_before": safe_payload.get("reminder_days_before"),
            "enabled": safe_payload.get("enabled", True),
        },
    ]

    seen: set[str] = set()
    errors: list[str] = []

    for attempt in attempts:
        cleaned = {k: v for k, v in attempt.items() if v is not None}
        if not cleaned.get("account_id") or not cleaned.get("tax_type") or not cleaned.get("due_date"):
            continue

        signature = repr(sorted(cleaned.keys()))
        if signature in seen:
            continue
        seen.add(signature)

        ok, resp, err = _safe_exec(supabase.table("tax_deadlines").insert(cleaned))
        if ok:
            rows = _rows(resp)
            row = rows[0] if rows else None
            if row and row.get("id"):
                reread = _telegram_deadline_get_by_id(str(row.get("id")))
                if reread:
                    row = reread

            return {
                "ok": True,
                "data": rows,
                "row": row,
                "mode": signature,
                "payload_keys": sorted(cleaned.keys()),
            }

        errors.append(str(err))

    logging.error("Telegram deadline insert failed: %s", errors[:3])
    return {
        "ok": False,
        "error": errors[-1] if errors else "insert_failed",
        "errors": errors[:3],
    }


def _telegram_deadline_update(deadline_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    deadline_id = _clean_text(deadline_id)
    if not deadline_id:
        return {"ok": False, "error": "missing_deadline_id"}

    safe_payload = _telegram_deadline_payload_for_table(payload, for_update=True)

    attempts: list[dict[str, Any]] = [
        dict(safe_payload),

        # Keep reminder_mode/reminder_time. Only remove less important optional fields.
        {
            k: v
            for k, v in safe_payload.items()
            if k not in {"reminder_email", "reminder_phone", "reminder_last_error", "last_reminder_sent_at"}
        },

        # If updated_at ever fails, remove it but still keep reminder_mode/reminder_time.
        {
            k: v
            for k, v in safe_payload.items()
            if k not in {"updated_at", "reminder_email", "reminder_phone", "reminder_last_error", "last_reminder_sent_at"}
        },

        # Last-resort base update. This should normally not be reached on the current schema.
        {
            "reminder_days_before": safe_payload.get("reminder_days_before"),
            "enabled": safe_payload.get("enabled", True),
        },
    ]

    errors: list[str] = []
    seen: set[str] = set()

    for attempt in attempts:
        cleaned = {k: v for k, v in attempt.items() if v is not None}
        if not cleaned:
            continue

        signature = repr(sorted(cleaned.keys()))
        if signature in seen:
            continue
        seen.add(signature)

        try:
            resp = (
                supabase.table("tax_deadlines")
                .update(cleaned)
                .eq("id", deadline_id)
                .execute()
            )

            persisted = _telegram_deadline_get_by_id(deadline_id)

            return {
                "ok": True,
                "data": _rows(resp),
                "row": persisted,
                "mode": signature,
                "payload_keys": sorted(cleaned.keys()),
            }

        except Exception as exc:
            errors.append(str(exc))

    logging.error("Telegram deadline update failed for %s: %s", deadline_id, errors[:3])
    return {
        "ok": False,
        "error": errors[-1] if errors else "update_failed",
        "errors": errors[:3],
    }

def _telegram_deadline_delete(deadline_id: str) -> dict[str, Any]:
    try:
        resp = supabase.table("tax_deadlines").delete().eq("id", deadline_id).execute()
        return {"ok": True, "data": _rows(resp)}
    except Exception as exc:
        logging.exception("Telegram deadline delete failed")
        return {"ok": False, "error": str(exc)}


def _create_deadline_reminder_telegram(chat_id: str, account_id: str, tg_user_id: str, text_raw: str) -> None:
    if not _deadline_allowed_for_account_telegram(account_id):
        send_telegram_text(
            chat_id,
            "🔒 *Custom Deadline Reminders*\n\n"
            "Custom deadline reminders are available on paid plans.\n\n"
            "Reply 4 to view plans, or D2 to view any reminders already saved.",
        )
        return

    parsed = _parse_deadline_create_telegram(text_raw)
    if not parsed:
        send_telegram_text(
            chat_id,
            "🔔 *Create Deadline Reminder*\n\n"
            "Send it like this:\n"
            "D1 PAYE 2026-06-10 3 09:00 telegram\n\n"
            "Format: D1 tax_type due_date reminder_days_before time mode\n"
            "Supported types: PAYE, VAT, CIT, WHT.\n"
            "Mode can be telegram, email, sms, or telegram,email.",
        )
        return

    validation = _deadline_validation_telegram(parsed["due_date"], parsed.get("reminder_days_before", 7))
    if not validation.get("ok"):
        max_days = validation.get("max_days", 0)
        send_telegram_text(
            chat_id,
            "⚠️ *Reminder Not Created*\n\n"
            f"{validation.get('message')}\n\n"
            f"Try: D1 {parsed['tax_type']} {parsed['due_date']} {max_days} {_valid_deadline_time(parsed.get('reminder_time'))} {_valid_deadline_mode(parsed.get('reminder_mode'))}\n"
            "Or choose a later due date.",
        )
        return

    now_iso = _utc_now_iso()
    payload = {
        "user_id": account_id,
        "account_id": account_id,
        "tax_type": parsed["tax_type"],
        "due_date": parsed["due_date"],
        "reminder_days_before": int(parsed.get("reminder_days_before") or 7),
        "enabled": True,
        "updated_at": now_iso,
        "reminder_time": _valid_deadline_time(parsed.get("reminder_time")),
        "timezone": "Africa/Lagos",
        "reminder_mode": _valid_deadline_mode(parsed.get("reminder_mode")),
        "reminder_phone": tg_user_id,
    }

    result = _telegram_deadline_insert(payload)
    if not result.get("ok"):
        send_telegram_text(
            chat_id,
            "⚠️ Reminder saving failed. Please try again.\n\n"
            f"{parsed['tax_type']} due date: {parsed['due_date']}\n"
            f"Reminder: {parsed['reminder_days_before']} days before\n\n"
            "If this repeats, contact support with SUP6.",
        )
        return

    persisted = result.get("row") if isinstance(result.get("row"), dict) else None
    if not persisted:
        rows = result.get("data") if isinstance(result.get("data"), list) else []
        persisted = rows[0] if rows and isinstance(rows[0], dict) else None

    requested_time = _valid_deadline_time(parsed.get("reminder_time"))
    requested_mode = _valid_deadline_mode(parsed.get("reminder_mode"))
    saved_time = _valid_deadline_time((persisted or {}).get("reminder_time") or requested_time)
    saved_mode = _valid_deadline_mode((persisted or {}).get("reminder_mode") or requested_mode)

    if saved_time != requested_time or saved_mode != requested_mode:
        send_telegram_text(
            chat_id,
            "⚠️ *Deadline Reminder Saved, But Please Confirm*\n\n"
            "The reminder was created, but the saved time/mode did not fully match the request.\n\n"
            f"Requested: {requested_time} via {requested_mode}\n"
            f"Saved: {saved_time} via {saved_mode}\n\n"
            "Reply D2 to view the saved reminder.",
        )
        return

    send_telegram_text(
        chat_id,
        "✅ *Deadline Reminder Saved*\n\n"
        f"Tax type: {parsed['tax_type']}\n"
        f"Due date: {parsed['due_date']}\n"
        f"Reminder: {parsed['reminder_days_before']} days before\n"
        f"Time: {saved_time}\n"
        f"Mode: {saved_mode}\n"
        f"Reminder date: {validation.get('reminder_date')}\n\n"
        "Reply D2 to view reminders or 0 for menu.",
    )


def _send_deadline_reminders_telegram(chat_id: str, account_id: str) -> None:
    rows = _telegram_deadline_rows(account_id, limit=10)

    if not rows:
        send_telegram_text(
            chat_id,
            "📋 *Your Deadline Reminders*\n\n"
            "No saved deadline reminders yet.\n\n"
            "Create one like this:\n"
            "D1 PAYE 2026-06-10 3",
        )
        return

    lines = ["📋 *Your Deadline Reminders*", ""]
    for idx, row in enumerate(rows, start=1):
        lines.append(_deadline_display_line_telegram(row, idx))

    lines.extend(
        [
            "",
            "To delete: D3 1",
            "To update: D4 1 3 09:00 telegram",
            "Reply 0 for main menu.",
        ]
    )
    send_telegram_text(chat_id, _clip_text("\n".join(lines), 3900))


def _delete_deadline_reminder_telegram(chat_id: str, account_id: str, text_raw: str) -> None:
    if not _deadline_allowed_for_account_telegram(account_id):
        send_telegram_text(
            chat_id,
            "🔒 Custom deadline management is available on paid plans.\n\n"
            "Reply 4 to view plans, or D2 to view any existing reminders.",
        )
        return

    match = re.search(r"\bD3\s+(\d{1,2})\b", _clean_text(text_raw), flags=re.I)
    if not match:
        send_telegram_text(
            chat_id,
            "🗑️ *Delete Deadline Reminder*\n\n"
            "Reply like this:\n"
            "D3 1\n\n"
            "Use D2 first to see your reminder numbers.",
        )
        return

    idx = int(match.group(1))
    row = _telegram_deadline_by_index(account_id, idx)
    if not row:
        send_telegram_text(chat_id, "I could not find that reminder number. Reply D2 to view your current reminders.")
        return

    deadline_id = _clean_text(row.get("id"))
    if not deadline_id:
        send_telegram_text(chat_id, "I found the reminder, but it has no valid ID. Please try D2 again or contact support.")
        return

    result = _telegram_deadline_delete(deadline_id)
    if not result.get("ok"):
        send_telegram_text(chat_id, "⚠️ I could not delete that reminder now. Reply D2 and try again, for example D3 1.")
        return

    send_telegram_text(
        chat_id,
        "🗑️ *Deadline Reminder Deleted*\n\n"
        f"{_deadline_display_line_telegram(row, idx)}\n\n"
        "Reply D2 to view remaining reminders.",
    )


def _update_deadline_reminder_telegram(chat_id: str, account_id: str, text_raw: str) -> None:
    if not _deadline_allowed_for_account_telegram(account_id):
        send_telegram_text(
            chat_id,
            "🔒 Custom reminder settings are available on paid plans.\n\n"
            "Reply 4 to view plans, or D2 to view any existing reminders.",
        )
        return

    match = re.search(
        r"\bD4\s+(\d{1,2})\s+(\d{1,3})(?:\s+(\d{1,2}:\d{2}))?(?:\s+([A-Z,]+))?\b",
        _clean_text(text_raw),
        flags=re.I,
    )

    if not match:
        send_telegram_text(
            chat_id,
            "⚙️ *Update Deadline Reminder*\n\n"
            "Reply like this:\n"
            "D4 1 3 09:00 telegram\n\n"
            "Use D2 first to see your reminder numbers.",
        )
        return

    idx = int(match.group(1))
    days = max(0, min(365, int(match.group(2))))
    reminder_time = _valid_deadline_time(match.group(3) or "09:00")
    reminder_mode = _valid_deadline_mode(match.group(4) or "telegram")

    row = _telegram_deadline_by_index(account_id, idx)
    if not row:
        send_telegram_text(
            chat_id,
            "I could not find that reminder number. Reply D2 to view your current reminders.",
        )
        return

    validation = _deadline_validation_telegram(row.get("due_date"), days)
    if not validation.get("ok"):
        send_telegram_text(
            chat_id,
            "⚠️ *Reminder Not Updated*\n\n"
            f"{validation.get('message')}\n\n"
            "Reply D2 to view your reminders.",
        )
        return

    deadline_id = _clean_text(row.get("id"))
    if not deadline_id:
        send_telegram_text(
            chat_id,
            "I found the reminder, but it has no valid ID. Please try D2 again or contact support.",
        )
        return

    payload = {
        "reminder_days_before": days,
        "enabled": True,
        "updated_at": _utc_now_iso(),
        "reminder_time": reminder_time,
        "timezone": "Africa/Lagos",
        "reminder_mode": reminder_mode,
    }

    result = _telegram_deadline_update(deadline_id, payload)
    if not result.get("ok"):
        send_telegram_text(
            chat_id,
            "⚠️ I could not update that reminder now. Reply D2 and try again.",
        )
        return

    persisted = result.get("row") if isinstance(result.get("row"), dict) else None
    if not persisted:
        persisted = _telegram_deadline_get_by_id(deadline_id)

    if not persisted:
        send_telegram_text(
            chat_id,
            "⚠️ Reminder update was attempted, but I could not re-read the saved record. Reply D2 to confirm.",
        )
        return

    saved_mode = _valid_deadline_mode(persisted.get("reminder_mode") or "")
    saved_time = _valid_deadline_time(persisted.get("reminder_time") or "")

    if saved_mode != reminder_mode or saved_time != reminder_time:
        send_telegram_text(
            chat_id,
            "⚠️ *Reminder Partly Updated*\n\n"
            "The reminder was saved, but the saved time/mode did not match the requested values.\n\n"
            f"Requested: {reminder_time} via {reminder_mode}\n"
            f"Saved: {saved_time} via {saved_mode}\n\n"
            "Reply D2 to view your current reminders.",
        )
        return

    send_telegram_text(
        chat_id,
        "⚙️ *Reminder Updated*\n\n"
        f"{_deadline_display_line_telegram(persisted, idx)}\n\n"
        "Reply D2 to view all reminders.",
    )

def _handle_deadline_command_telegram(chat_id: str, account_id: str, tg_user_id: str, text_raw: str) -> bool:
    text_clean = _clean_text(text_raw)
    cmd_match = re.match(r"^(D[1-4])\b", text_clean, flags=re.I)
    cmd = cmd_match.group(1).upper() if cmd_match else ""

    if not cmd:
        return False

    if cmd == "D1":
        # D1 alone opens help/menu. D1 with details creates a reminder.
        if re.search(r"\bD1\s+(PAYE|VAT|CIT|WHT)\b", text_clean, flags=re.I):
            _create_deadline_reminder_telegram(chat_id, account_id, tg_user_id, text_clean)
        else:
            send_telegram_text(chat_id, _telegram_deadline_usage_text(account_id))
        return True

    if cmd == "D2":
        _send_deadline_reminders_telegram(chat_id, account_id)
        return True

    if cmd == "D3":
        _delete_deadline_reminder_telegram(chat_id, account_id, text_clean)
        return True

    if cmd == "D4":
        _update_deadline_reminder_telegram(chat_id, account_id, text_clean)
        return True

    return False




# ---------------------------------------------------------------------------
# Batch 28D - Telegram Quiz Q1-Q5 parity
# ---------------------------------------------------------------------------

QUIZ_FREE_DAILY_LIMIT = 12

TELEGRAM_QUIZ_FALLBACK_BANK: list[dict[str, Any]] = [
    {
        "id": "tg_q_paye_1",
        "category": "PAYE",
        "question": "Which Nigerian tax is normally deducted from employee salaries by the employer?",
        "options": {"A": "VAT", "B": "PAYE", "C": "Company Income Tax", "D": "Import Duty"},
        "answer": "B",
        "explain": "PAYE is deducted from employment income and remitted by the employer.",
        "premium_explanation": "PAYE means Pay-As-You-Earn. In practice, the employer deducts tax from the employee's salary and remits it to the relevant State Internal Revenue Service. This helps employees meet Personal Income Tax obligations through payroll instead of waiting until year-end.",
    },
    {
        "id": "tg_q_paye_2",
        "category": "PAYE",
        "question": "Who normally remits PAYE after deducting it from salaries?",
        "options": {"A": "The employee's landlord", "B": "The employer", "C": "The customer", "D": "The bank cashier"},
        "answer": "B",
        "explain": "The employer deducts PAYE and remits it to the relevant State Internal Revenue Service.",
        "premium_explanation": "The employer acts as the withholding/remitting party for PAYE. Employees bear the tax economically, but the employer is responsible for deduction and remittance from payroll to the appropriate State Internal Revenue Service.",
    },
    {
        "id": "tg_q_vat_1",
        "category": "VAT",
        "question": "What is the standard VAT rate commonly used in Nigeria for many taxable supplies?",
        "options": {"A": "2.5%", "B": "5%", "C": "7.5%", "D": "30%"},
        "answer": "C",
        "explain": "Nigeria's standard VAT rate is commonly 7.5% for many taxable supplies.",
        "premium_explanation": "VAT is a consumption tax charged on many taxable goods and services. For many ordinary VATable supplies in Nigeria, the standard rate is 7.5%. The business usually charges output VAT and may deduct allowable input VAT before remitting the net amount.",
    },
    {
        "id": "tg_q_vat_2",
        "category": "VAT",
        "question": "Which authority generally administers VAT in Nigeria?",
        "options": {"A": "FIRS", "B": "Local government chairman", "C": "FRSC", "D": "NIMC"},
        "answer": "A",
        "explain": "VAT is generally administered by the Federal Inland Revenue Service.",
        "premium_explanation": "VAT is generally administered by the Federal Inland Revenue Service. A VAT-registered business should understand registration, invoicing, output VAT, input VAT, returns, and remittance obligations before filing.",
    },
    {
        "id": "tg_q_cit_1",
        "category": "Company Tax",
        "question": "Company Income Tax is mainly charged on what?",
        "options": {"A": "Company taxable profit", "B": "Employee school fees", "C": "Bank BVN", "D": "Personal rent only"},
        "answer": "A",
        "explain": "Company Income Tax is generally based on company taxable profit.",
        "premium_explanation": "Company Income Tax applies to the taxable profits of companies after relevant adjustments, allowances, and exemptions. Turnover category can also affect applicable rates, especially for small, medium, and large company classification.",
    },
    {
        "id": "tg_q_wht_1",
        "category": "WHT",
        "question": "Withholding Tax is best described as what?",
        "options": {"A": "A deduction at source from certain payments", "B": "A vehicle license", "C": "A bank password", "D": "A pension card"},
        "answer": "A",
        "explain": "WHT is deducted at source from certain qualifying payments.",
        "premium_explanation": "Withholding Tax is deducted at source by the payer from certain qualifying payments. It is usually remitted to the relevant tax authority and may serve as advance tax credit for the recipient, depending on the transaction and taxpayer type.",
    },
    {
        "id": "tg_q_records_1",
        "category": "Records",
        "question": "Why should a business keep PAYE records?",
        "options": {"A": "To show salary, deductions, and remittances", "B": "To decorate the office", "C": "To replace bank accounts", "D": "To avoid all tax forever"},
        "answer": "A",
        "explain": "PAYE records help show salaries paid, deductions made, and remittances to the relevant tax authority.",
        "premium_explanation": "PAYE records support payroll compliance. They help show gross salary, deductions, reliefs, tax calculated, payment dates, and remittance evidence. Good records also make audits and employee tax-clearance support easier.",
    },
    {
        "id": "tg_q_deadlines_1",
        "category": "Deadlines",
        "question": "If a reminder date has already passed, what should a reminder system do?",
        "options": {"A": "Reject it or suggest a valid reminder period", "B": "Accept it silently", "C": "Delete all reminders", "D": "Charge VAT automatically"},
        "answer": "A",
        "explain": "A reminder should only be accepted if the reminder date can still occur today or in the future.",
        "premium_explanation": "A deadline reminder is useful only when it can still notify the user before the due date. If the calculated reminder date has passed, the system should reject it or suggest the maximum valid reminder period so users do not rely on an impossible alert.",
    },
]


def _quiz_norm(value: Any) -> str:
    return re.sub(r"\s+", " ", _clean_text(value).lower()).strip()


def _quiz_today_key() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _quiz_is_paid(account_id: str) -> bool:
    try:
        return bool(has_active_subscription(account_id))
    except Exception:
        row = _subscription_row(account_id)
        if not row:
            return False
        status = _clean_text(row.get("status")).lower()
        return status == "active"


def _quiz_text_telegram() -> str:
    return (
        "🧠 *Tax Quiz*\n\n"
        "Q1 - Start random quiz\n"
        "Q2 - Choose category\n"
        "Q3 - View today's score\n"
        "Q4 - Review last answer\n"
        "Q5 - Detailed saved explanation\n\n"
        "Reply A, B, C, or D after a question.\n"
        "Free users get 12 non-AI quiz attempts daily. Paid users get unlimited non-AI quiz attempts.\n"
        "Q5 costs 1 Usage Credit and uses saved explanations, not live AI."
    )


def _quiz_state(chat_id: str) -> dict[str, Any]:
    state = user_states.get(chat_id)
    if not isinstance(state, dict):
        state = {}
    data = state.get("quiz_data")
    if not isinstance(data, dict):
        data = {}
    state["quiz_data"] = data
    return state


def _quiz_data(chat_id: str) -> dict[str, Any]:
    state = _quiz_state(chat_id)
    data = state.get("quiz_data")
    return data if isinstance(data, dict) else {}


def _quiz_daily_numbers_from_data(data: dict[str, Any]) -> dict[str, int]:
    today = _quiz_today_key()
    if data.get("quiz_date") != today:
        return {"attempts": 0, "correct": 0, "wrong": 0}
    return {
        "attempts": int(data.get("quiz_attempts") or 0),
        "correct": int(data.get("quiz_correct_count") or 0),
        "wrong": int(data.get("quiz_wrong_count") or 0),
    }


def _quiz_categories_telegram() -> list[str]:
    categories: set[str] = set()
    try:
        res = supabase.table("tax_quiz_questions").select("category").eq("is_active", True).limit(200).execute()
        for row in _rows(res):
            category = _clean_text(row.get("category"))
            if category:
                categories.add(category)
    except Exception:
        pass

    for row in TELEGRAM_QUIZ_FALLBACK_BANK:
        category = _clean_text(row.get("category") or "General")
        if category:
            categories.add(category)

    return sorted(categories or {"General"})


def _quiz_category_menu_telegram() -> str:
    lines = ["🧠 *Choose Quiz Category*", ""]
    categories = _quiz_categories_telegram()
    for idx, category in enumerate(categories, start=1):
        shortcut = category.upper().replace("COMPANY TAX", "CIT")
        lines.append(f"Q2 {idx} - {category}  |  Q1 {shortcut}")

    lines.extend(
        [
            "",
            "Examples:",
            "Q1 PAYE",
            "Q1 VAT",
            "Q1 CIT",
            "Q1 WHT",
            "",
            "Reply Q1 for mixed random quiz or 0 for menu.",
        ]
    )
    return "\n".join(lines)


def _normalize_quiz_category_telegram(value: Any) -> str:
    norm = _quiz_norm(value)
    if not norm:
        return ""

    aliases = {
        "cit": "Company Tax",
        "company": "Company Tax",
        "company tax": "Company Tax",
        "company income tax": "Company Tax",
        "paye": "PAYE",
        "pay as you earn": "PAYE",
        "vat": "VAT",
        "wht": "WHT",
        "withholding": "WHT",
        "withholding tax": "WHT",
        "deadline": "Deadlines",
        "deadlines": "Deadlines",
        "record": "Records",
        "records": "Records",
        "general": "General",
    }
    if norm in aliases:
        return aliases[norm]

    for category in _quiz_categories_telegram():
        if norm == _quiz_norm(category):
            return category

    return ""


def _resolve_quiz_category_telegram(text: str) -> str:
    norm = _quiz_norm(text)
    categories = _quiz_categories_telegram()

    if norm in {"q1", "quiz", "start quiz", "tax quiz", "quiz me", "take quiz", "q2", "category", "quiz categories"}:
        return ""

    match_num = re.match(r"^q[12]\s+(\d{1,2})$", norm)
    if match_num:
        idx = int(match_num.group(1)) - 1
        return categories[idx] if 0 <= idx < len(categories) else ""

    cleaned = re.sub(r"^q[12]\s+", "", norm).strip()
    if cleaned in {"mixed", "random", "all", "general all"}:
        return ""

    return _normalize_quiz_category_telegram(cleaned)


def _quiz_question_from_db_row_telegram(row: dict[str, Any]) -> dict[str, Any]:
    short = _clean_text(row.get("short_explanation") or row.get("explain"))
    premium = _clean_text(row.get("premium_explanation") or short)
    return {
        "source": "db",
        "db_id": _clean_text(row.get("id")),
        "id": _clean_text(row.get("question_code") or row.get("id")),
        "question_code": _clean_text(row.get("question_code") or row.get("id")),
        "category": _clean_text(row.get("category") or "General"),
        "difficulty": _clean_text(row.get("difficulty") or "basic"),
        "question": _clean_text(row.get("question")),
        "short_explanation": short,
        "explain": short,
        "premium_explanation": premium,
        "source_reference": _clean_text(row.get("source_reference") or "Naija Tax Guide quiz bank"),
    }


def _load_quiz_questions_telegram(category: str = "") -> list[dict[str, Any]]:
    category = _clean_text(category)
    db_rows: list[dict[str, Any]] = []

    try:
        query = supabase.table("tax_quiz_questions").select(
            "id,question_code,category,difficulty,question,short_explanation,premium_explanation,source_reference,is_active,created_at"
        ).eq("is_active", True)
        if category:
            query = query.eq("category", category)
        res = query.limit(120).execute()
        db_rows = [row for row in _rows(res) if _clean_text(row.get("question"))]
    except Exception:
        db_rows = []

    questions = [_quiz_question_from_db_row_telegram(row) for row in db_rows]
    if questions:
        return questions

    fallback = [dict(row) for row in TELEGRAM_QUIZ_FALLBACK_BANK]
    if category:
        wanted = _quiz_norm(category)
        filtered = [row for row in fallback if _quiz_norm(row.get("category")) == wanted]
        return filtered or fallback
    return fallback


def _load_db_quiz_options_telegram(question: dict[str, Any]) -> list[dict[str, Any]]:
    db_id = _clean_text(question.get("db_id"))
    if not db_id:
        return []

    try:
        res = (
            supabase.table("tax_quiz_options")
            .select("id,option_code,option_text,is_correct,created_at")
            .eq("question_id", db_id)
            .limit(20)
            .execute()
        )
        rows = _rows(res)
    except Exception:
        rows = []

    options: list[dict[str, Any]] = []
    for row in rows:
        text = _clean_text(row.get("option_text"))
        if not text:
            continue
        options.append(
            {
                "option_id": _clean_text(row.get("id") or row.get("option_code") or text),
                "option_text": text,
                "is_correct": bool(row.get("is_correct")),
                "source_code": _clean_text(row.get("option_code")),
            }
        )
    return options


def _load_static_quiz_options_telegram(question: dict[str, Any]) -> list[dict[str, Any]]:
    raw_options = question.get("options") if isinstance(question.get("options"), dict) else {}
    correct = _clean_text(question.get("answer") or question.get("correct")).upper()[:1]

    options: list[dict[str, Any]] = []
    for key, value in raw_options.items():
        label = _clean_text(key).upper()[:1]
        text = _clean_text(value)
        if not label or not text:
            continue
        options.append(
            {
                "option_id": f"{_clean_text(question.get('id') or question.get('question_code'))}_{label}",
                "option_text": text,
                "is_correct": label == correct,
                "source_code": label,
            }
        )
    return options


def _static_premium_explanation_telegram(question: dict[str, Any]) -> str:
    base = _clean_text(question.get("premium_explanation") or question.get("short_explanation") or question.get("explain"))
    category = _clean_text(question.get("category") or "General")
    if not base:
        base = "This quiz item is part of the Nigerian tax compliance learning bank."
    return _clip_text(
        f"{base}\n\n"
        f"Practical example: This helps you apply the correct {category} rule, deadline, record, or filing step in a real Nigerian tax situation.\n\n"
        "Why it matters: Understanding the rule helps reduce wrong answers, missed steps, and avoidable compliance mistakes.",
        1200,
    )


def _randomized_quiz_payload_telegram(question: dict[str, Any]) -> dict[str, Any]:
    options = _load_db_quiz_options_telegram(question) if question.get("source") == "db" else []
    if not options:
        options = _load_static_quiz_options_telegram(question)
    if not options:
        options = [
            {"option_id": f"{_clean_text(question.get('id'))}_A", "option_text": "True", "is_correct": True, "source_code": "A"},
            {"option_id": f"{_clean_text(question.get('id'))}_B", "option_text": "False", "is_correct": False, "source_code": "B"},
        ]

    rng = random.SystemRandom()
    shuffled = list(options)
    rng.shuffle(shuffled)

    labels = ["A", "B", "C", "D"]
    display_options: dict[str, str] = {}
    option_order: dict[str, str] = {}
    correct_label = ""
    correct_option_id = ""

    for label, option in zip(labels, shuffled[:4]):
        option_id = _clean_text(option.get("option_id") or option.get("source_code") or label)
        option_text = _clean_text(option.get("option_text"))
        if not option_text:
            continue
        display_options[label] = option_text
        option_order[label] = option_id
        if bool(option.get("is_correct")):
            correct_label = label
            correct_option_id = option_id

    if not correct_label and display_options:
        correct_label = next(iter(display_options.keys()))
        correct_option_id = option_order.get(correct_label, correct_label)

    short = _clean_text(question.get("short_explanation") or question.get("explain"))
    premium = _clean_text(question.get("premium_explanation")) or _static_premium_explanation_telegram(question)
    if not short:
        short = premium

    return {
        **question,
        "options": display_options,
        "option_order": option_order,
        "correct": correct_label,
        "answer": correct_label,
        "correct_option_id": correct_option_id,
        "short_explanation": short,
        "explain": short,
        "premium_explanation": premium,
    }


def _select_quiz_question_telegram(pool: list[dict[str, Any]], data: dict[str, Any], category: str) -> dict[str, Any]:
    if not pool:
        pool = [dict(row) for row in TELEGRAM_QUIZ_FALLBACK_BANK]

    key = "quiz_seen_ids_" + (_quiz_norm(category or "mixed").replace(" ", "_") or "mixed")
    seen = data.get(key)
    if not isinstance(seen, list):
        seen = []

    def qid(row: dict[str, Any]) -> str:
        return _clean_text(row.get("id") or row.get("question_code") or row.get("db_id") or row.get("question"))

    available = [row for row in pool if qid(row) not in seen]
    if not available:
        seen = []
        available = list(pool)

    rng = random.SystemRandom()
    selected = dict(rng.choice(available))
    current_id = qid(selected)
    if current_id:
        seen.append(current_id)

    data[key] = seen[-80:]
    return selected


def _quiz_safe_update_by_id(table: str, row_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not _clean_text(row_id):
        return {"ok": False, "error": "missing_id"}

    payloads = [
        dict(payload),
        {k: v for k, v in payload.items() if k != "updated_at"},
        {k: v for k, v in payload.items() if k not in {"updated_at", "metadata"}},
    ]

    errors: list[str] = []
    seen: set[str] = set()

    for candidate in payloads:
        cleaned = {k: v for k, v in candidate.items() if v is not None}
        signature = repr(sorted(cleaned.keys()))
        if signature in seen:
            continue
        seen.add(signature)
        ok, resp, err = _safe_exec(supabase.table(table).update(cleaned).eq("id", row_id))
        if ok:
            return {"ok": True, "data": _rows(resp), "mode": signature}
        errors.append(str(err))

    return {"ok": False, "error": errors[-1] if errors else "update_failed", "errors": errors[:3]}


def _quiz_safe_insert_telegram(table: str, payload: dict[str, Any]) -> dict[str, Any]:
    """
    Batch 28D3:
    Reduce Supabase 400 retry noise by trying the known accepted quiz-attempt
    schema first.

    From Batch 28D2 logs, tax_quiz_attempts accepted the minimal payload after
    wider payloads failed. We now send that accepted payload first.
    """
    if table == "tax_quiz_attempts":
        payloads = [
            {
                "account_id": payload.get("account_id"),
                "question_id": payload.get("question_id"),
                "question_code": payload.get("question_code"),
                "category": payload.get("category"),
                "status": payload.get("status"),
                "created_at": payload.get("created_at"),
            },
            {
                "account_id": payload.get("account_id"),
                "question_id": payload.get("question_id"),
                "question_code": payload.get("question_code"),
                "category": payload.get("category"),
                "status": payload.get("status"),
                "created_at": payload.get("created_at"),
                "channel": payload.get("channel"),
            },
            {k: v for k, v in payload.items() if k not in {"updated_at", "metadata", "displayed_option_order", "correct_option_id", "provider_user_id", "tg_user_id"}},
            {k: v for k, v in payload.items() if k not in {"updated_at", "metadata"}},
            dict(payload),
        ]
    else:
        payloads = [
            dict(payload),
            {k: v for k, v in payload.items() if k != "updated_at"},
            {k: v for k, v in payload.items() if k not in {"updated_at", "metadata"}},
        ]

    errors: list[str] = []
    seen: set[str] = set()
    for candidate in payloads:
        cleaned = {k: v for k, v in candidate.items() if v is not None}
        signature = repr(sorted(cleaned.keys()))
        if signature in seen:
            continue
        seen.add(signature)
        ok, resp, err = _safe_exec(supabase.table(table).insert(cleaned))
        if ok:
            return {"ok": True, "data": _rows(resp), "mode": signature}
        errors.append(str(err))

    return {"ok": False, "error": errors[-1] if errors else "insert_failed", "errors": errors[:3]}


def _log_quiz_attempt_started_telegram(account_id: str, tg_user_id: str, quiz: dict[str, Any]) -> str:
    payload = {
        "account_id": account_id,
        "tg_user_id": tg_user_id,
        "provider_user_id": tg_user_id,
        "channel": "telegram",
        "question_id": _clean_text(quiz.get("db_id")) or None,
        "question_code": _clean_text(quiz.get("question_code") or quiz.get("id")) or None,
        "category": _clean_text(quiz.get("category") or "General"),
        "displayed_option_order": quiz.get("option_order") if isinstance(quiz.get("option_order"), dict) else {},
        "correct_option_id": _clean_text(quiz.get("correct_option_id")) or None,
        "status": "started",
        "created_at": _utc_now_iso(),
        "updated_at": _utc_now_iso(),
        "metadata": {"source": _clean_text(quiz.get("source") or "static"), "platform": "telegram"},
    }
    result = _quiz_safe_insert_telegram("tax_quiz_attempts", payload)
    rows = result.get("data") if isinstance(result.get("data"), list) else []
    if rows and isinstance(rows[0], dict):
        return _clean_text(rows[0].get("id"))
    return ""


def _log_quiz_attempt_answered_telegram(attempt_id: str, answer: str, selected_option_id: str, passed: bool) -> None:
    _quiz_safe_update_by_id(
        "tax_quiz_attempts",
        attempt_id,
        {
            "selected_label": _clean_text(answer).upper()[:1],
            "selected_option_id": _clean_text(selected_option_id) or None,
            "is_correct": bool(passed),
            "status": "answered",
            "answered_at": _utc_now_iso(),
            "updated_at": _utc_now_iso(),
        },
    )


def _log_quiz_q5_used_telegram(attempt_id: str, debit: dict[str, Any]) -> None:
    _quiz_safe_update_by_id(
        "tax_quiz_attempts",
        attempt_id,
        {
            "q5_explanation_used": True,
            "credits_charged": int(debit.get("credits_consumed") or 1),
            "q5_explained_at": _utc_now_iso(),
            "updated_at": _utc_now_iso(),
        },
    )


def _quiz_balance_row(account_id: str) -> tuple[Optional[dict[str, Any]], str]:
    try:
        resp = supabase.table("ai_credit_balances").select("*").eq("account_id", account_id).limit(1).execute()
        row = _first(resp)
        if row:
            return row, ""
    except Exception as exc:
        return None, str(exc)
    return None, "credit_balance_not_found"


def _quiz_credit_column(row: dict[str, Any]) -> str:
    for key in ("balance", "credits", "credit_balance", "available_credits"):
        if key in row:
            return key
    return "balance"


def _insert_quiz_credit_log(account_id: str, before: int, after: int, attempt_id: str) -> dict[str, Any]:
    """
    Batch 28D3:
    Write Q5 deduction to credit_usage_logs with the accepted schema first.

    Batch 28D2 proved the third payload worked:
      account_id, action_code, description, credits_delta, created_at

    So Batch 28D3 tries that first to remove avoidable 400 retry noise.
    """
    now_iso = _utc_now_iso()
    reference = f"Q5-TG-{uuid.uuid4().hex[:12].upper()}"

    payload_attempts: list[dict[str, Any]] = [
        {
            "account_id": account_id,
            "action_code": "quiz_q5_saved_explanation",
            "description": "Telegram Q5 detailed saved quiz explanation",
            "credits_delta": -1,
            "created_at": now_iso,
        },
        {
            "account_id": account_id,
            "description": "Telegram Q5 detailed saved quiz explanation",
            "credits_delta": -1,
            "created_at": now_iso,
        },
        {
            "account_id": account_id,
            "description": "Telegram Q5 detailed saved quiz explanation",
            "amount": -1,
            "created_at": now_iso,
        },
        {
            "account_id": account_id,
            "reference": reference,
            "action_code": "quiz_q5_saved_explanation",
            "description": "Telegram Q5 detailed saved quiz explanation",
            "channel": "telegram",
            "credits_delta": -1,
            "amount": -1,
            "balance_before": before,
            "balance_after": after,
            "metadata": {"source": "telegram_quiz", "live_ai_called": False, "attempt_id": attempt_id},
            "created_at": now_iso,
        },
    ]

    errors: list[str] = []
    seen: set[str] = set()

    for payload in payload_attempts:
        cleaned = {k: v for k, v in payload.items() if v is not None}
        signature = repr(sorted(cleaned.keys()))
        if signature in seen:
            continue
        seen.add(signature)

        ok, resp, err = _safe_exec(supabase.table("credit_usage_logs").insert(cleaned))
        if ok:
            return {
                "ok": True,
                "table": "credit_usage_logs",
                "mode": signature,
                "reference": reference,
                "rows": _rows(resp),
            }
        errors.append(str(err))

    # Do not fail Q5 if the log table schema is still narrower than expected.
    # CR3 will still show Q5 deductions from tax_quiz_attempts after the Q5
    # attempt update succeeds.
    logging.warning("Telegram Q5 credit_usage_logs insert failed; CR3 will use quiz-attempt fallback: %s", errors[:2])
    return {"ok": False, "table": "credit_usage_logs", "error": errors[-1] if errors else "insert_failed", "errors": errors[:2]}


def _debit_q5_usage_credit_telegram(account_id: str, attempt_id: str = "") -> dict[str, Any]:
    row, err = _quiz_balance_row(account_id)
    if not row:
        return {"ok": False, "error": err or "credit_balance_not_found", "mode": "no_balance_row"}

    column = _quiz_credit_column(row)
    try:
        before = int(row.get(column) or 0)
    except Exception:
        before = 0

    if before < 1:
        return {"ok": False, "error": "insufficient_credits", "before": before, "after": before, "column": column}

    after = before - 1
    payloads = [
        {column: after, "updated_at": _utc_now_iso()},
        {column: after},
    ]

    errors: list[str] = []
    for payload in payloads:
        ok, _resp, err = _safe_exec(supabase.table("ai_credit_balances").update(payload).eq("account_id", account_id))
        if ok:
            credit_log = _insert_quiz_credit_log(account_id, before, after, attempt_id)
            return {"ok": True, "before": before, "after": after, "column": column, "credits_consumed": 1, "credit_log": credit_log}
        errors.append(str(err))

    return {"ok": False, "error": errors[-1] if errors else "credit_update_failed", "before": before, "after": before, "column": column}



TELEGRAM_QUIZ_STATE_METADATA_KEY = "telegram_quiz_state_v1"


def _telegram_quiz_compact_state(chat_id: str, state: dict[str, Any]) -> dict[str, Any]:
    data = state.get("quiz_data") if isinstance(state.get("quiz_data"), dict) else {}
    return {
        "chat_id": _clean_text(chat_id),
        "quiz_mode": _clean_text(state.get("quiz_mode")),
        "quiz_data": data,
        "saved_at": _utc_now_iso(),
        "version": "28H",
    }


def _telegram_quiz_state_is_fresh(saved: dict[str, Any]) -> bool:
    saved_at = _parse_dt(saved.get("saved_at"))
    if not saved_at:
        return False
    try:
        return (datetime.now(timezone.utc) - saved_at).total_seconds() <= 86400
    except Exception:
        return False


def _load_telegram_quiz_state(tg_user_id: str, chat_id: str = "") -> dict[str, Any]:
    """
    Load Telegram quiz state from channel_identities.metadata.

    Why this is needed:
    Koyeb/Gunicorn can route Q1 and the later A/B/C/D answer to different
    workers. Plain in-memory user_states is not reliable across workers.
    """
    tg_user_id = _clean_text(tg_user_id)
    if not tg_user_id:
        return {}

    identity = _get_telegram_identity(tg_user_id)
    if not identity:
        return {}

    metadata = identity.get("metadata") if isinstance(identity.get("metadata"), dict) else {}
    saved = metadata.get(TELEGRAM_QUIZ_STATE_METADATA_KEY)
    if not isinstance(saved, dict):
        return {}

    if not _telegram_quiz_state_is_fresh(saved):
        return {}

    saved_chat_id = _clean_text(saved.get("chat_id"))
    if chat_id and saved_chat_id and saved_chat_id != _clean_text(chat_id):
        return {}

    data = saved.get("quiz_data") if isinstance(saved.get("quiz_data"), dict) else {}
    if not data:
        return {}

    return {
        "quiz_mode": _clean_text(saved.get("quiz_mode")),
        "quiz_data": data,
    }


def _save_telegram_quiz_state(tg_user_id: str, chat_id: str, state: dict[str, Any]) -> None:
    tg_user_id = _clean_text(tg_user_id)
    if not tg_user_id:
        return

    identity = _get_telegram_identity(tg_user_id)
    if not identity:
        return

    identity_id = _clean_text(identity.get("id"))
    if not identity_id:
        return

    metadata = identity.get("metadata") if isinstance(identity.get("metadata"), dict) else {}
    metadata[TELEGRAM_QUIZ_STATE_METADATA_KEY] = _telegram_quiz_compact_state(chat_id, state)

    # Batch 28D3:
    # Current channel_identities schema accepts metadata-only first. Avoid
    # unnecessary PATCH 400 noise from updated_at on this table.
    payload_attempts = [
        {"metadata": metadata},
        {"metadata": metadata, "updated_at": _utc_now_iso()},
    ]

    for payload in payload_attempts:
        ok, _resp, _err = _safe_exec(supabase.table("channel_identities").update(payload).eq("id", identity_id))
        if ok:
            return

    logging.warning("Telegram quiz state persistence failed for %s", tg_user_id)


def _clear_telegram_quiz_state(tg_user_id: str) -> None:
    tg_user_id = _clean_text(tg_user_id)
    if not tg_user_id:
        return

    identity = _get_telegram_identity(tg_user_id)
    if not identity:
        return

    identity_id = _clean_text(identity.get("id"))
    if not identity_id:
        return

    metadata = identity.get("metadata") if isinstance(identity.get("metadata"), dict) else {}
    if TELEGRAM_QUIZ_STATE_METADATA_KEY not in metadata:
        return

    metadata.pop(TELEGRAM_QUIZ_STATE_METADATA_KEY, None)

    for payload in ({"metadata": metadata}, {"metadata": metadata, "updated_at": _utc_now_iso()}):
        ok, _resp, _err = _safe_exec(supabase.table("channel_identities").update(payload).eq("id", identity_id))
        if ok:
            return



def _start_quiz_telegram(chat_id: str, account_id: str, tg_user_id: str, category: str = "") -> None:
    state = _quiz_state(chat_id)
    data = state.get("quiz_data") if isinstance(state.get("quiz_data"), dict) else {}
    today = _quiz_today_key()
    numbers = _quiz_daily_numbers_from_data(data)

    if data.get("quiz_date") != today:
        data = {"quiz_date": today, "quiz_attempts": 0, "quiz_correct_count": 0, "quiz_wrong_count": 0}
        numbers = {"attempts": 0, "correct": 0, "wrong": 0}

    if not _quiz_is_paid(account_id) and numbers["attempts"] >= QUIZ_FREE_DAILY_LIMIT:
        send_telegram_text(
            chat_id,
            "🔒 *Daily Quiz Limit Reached*\n\n"
            f"Free users can take {QUIZ_FREE_DAILY_LIMIT} non-AI quiz attempts daily.\n"
            "Paid users get unlimited non-AI quiz attempts.\n\n"
            "Reply 4 to view plans or 0 for menu.",
        )
        return

    resolved_category = _resolve_quiz_category_telegram(category or "")
    pool = _load_quiz_questions_telegram(resolved_category)
    base_question = _select_quiz_question_telegram(pool, data, resolved_category)
    quiz = _randomized_quiz_payload_telegram(base_question)
    attempt_id = _log_quiz_attempt_started_telegram(account_id, tg_user_id, quiz)

    data.update(
        {
            "quiz_date": today,
            "quiz_attempts": numbers["attempts"],
            "quiz_id": quiz.get("id"),
            "quiz_db_id": quiz.get("db_id"),
            "quiz_attempt_id": attempt_id,
            "quiz_category": quiz.get("category"),
            "quiz_question": quiz.get("question"),
            "quiz_options": quiz.get("options"),
            "quiz_option_order": quiz.get("option_order"),
            "correct": quiz.get("correct"),
            "correct_option_id": quiz.get("correct_option_id"),
            "explain": quiz.get("short_explanation") or quiz.get("explain"),
            "premium_explain": quiz.get("premium_explanation"),
            "source_reference": quiz.get("source_reference"),
            "active_quiz_started_at": _utc_now_iso(),
        }
    )

    user_states[chat_id] = {
        **state,
        "quiz_mode": "answer",
        "quiz_data": data,
    }
    _save_telegram_quiz_state(tg_user_id, chat_id, user_states[chat_id])

    options = "\n".join([f"{key}. {value}" for key, value in (quiz.get("options") or {}).items()])
    remaining = "Unlimited" if _quiz_is_paid(account_id) else str(max(0, QUIZ_FREE_DAILY_LIMIT - numbers["attempts"]))

    body = (
        f"🧠 *Tax Quiz* ({quiz.get('category') or 'General'})\n\n"
        f"Question: {quiz.get('question')}\n\n"
        f"{options}\n\n"
        f"Remaining today: {remaining}\n\n"
        "Reply A, B, C, or D.\n"
        "Reply CANCEL to stop."
    )
    send_telegram_text(chat_id, _clip_text(body, 3900))


def _handle_quiz_answer_telegram(chat_id: str, account_id: str, text: str, tg_user_id: str = "") -> bool:
    answer = _clean_text(text).upper()[:1]

    # Batch 28H fix:
    # Koyeb/Gunicorn may route the Q1 message and the later A/B/C/D answer to
    # different workers. Always reload the persisted quiz state before grading so
    # the answer is checked against the exact question last sent to the user, not
    # a stale in-memory worker copy.
    persisted_state = _load_telegram_quiz_state(tg_user_id, chat_id) if _clean_text(tg_user_id) else {}
    if persisted_state:
        state = persisted_state
        user_states[chat_id] = state
    else:
        state = _quiz_state(chat_id)

    data = state.get("quiz_data") if isinstance(state.get("quiz_data"), dict) else {}

    if _quiz_norm(text) in {"cancel", "stop", "end"}:
        state.pop("quiz_mode", None)
        user_states[chat_id] = state
        _clear_telegram_quiz_state(tg_user_id)
        send_telegram_text(chat_id, "Quiz cancelled.\n\nReply Q1 to start again or 0 for menu.")
        return True

    if answer not in {"A", "B", "C", "D"}:
        send_telegram_text(chat_id, "Please reply with A, B, C, or D.\n\nReply CANCEL to stop the quiz.")
        return True

    correct = _clean_text(data.get("correct")).upper()[:1] or "A"
    option_order = data.get("quiz_option_order") if isinstance(data.get("quiz_option_order"), dict) else {}
    selected_option_id = _clean_text(option_order.get(answer) or answer)
    correct_option_id = _clean_text(data.get("correct_option_id") or option_order.get(correct) or correct)

    numbers = _quiz_daily_numbers_from_data(data)
    attempts = numbers["attempts"] + 1
    passed = bool(selected_option_id and correct_option_id and selected_option_id == correct_option_id) if option_order else answer == correct
    correct_count = numbers["correct"] + (1 if passed else 0)
    wrong_count = numbers["wrong"] + (0 if passed else 1)

    attempt_id = _clean_text(data.get("quiz_attempt_id"))
    _log_quiz_attempt_answered_telegram(attempt_id, answer, selected_option_id, passed)

    options = data.get("quiz_options") if isinstance(data.get("quiz_options"), dict) else {}
    selected_text = _clean_text(options.get(answer))
    correct_text = _clean_text(options.get(correct))

    explain = _clean_text(data.get("explain"))
    premium = _clean_text(data.get("premium_explain") or explain)
    last_quiz = {
        "id": data.get("quiz_id"),
        "db_id": data.get("quiz_db_id"),
        "attempt_id": attempt_id,
        "category": data.get("quiz_category"),
        "question": data.get("quiz_question"),
        "options": options,
        "option_order": option_order,
        "selected": answer,
        "selected_text": selected_text,
        "selected_option_id": selected_option_id,
        "correct": correct,
        "correct_text": correct_text,
        "correct_option_id": correct_option_id,
        "is_correct": passed,
        "explanation": explain,
        "premium_explanation": premium,
        "source_reference": data.get("source_reference"),
        "answered_at": _utc_now_iso(),
    }

    data.update(
        {
            "quiz_date": _quiz_today_key(),
            "quiz_attempts": attempts,
            "quiz_correct_count": correct_count,
            "quiz_wrong_count": wrong_count,
            "last_quiz": last_quiz,
            "last_quiz_answer": answer,
            "last_quiz_correct": correct,
            "last_quiz_passed": passed,
            "last_quiz_explain": explain,
            "last_quiz_premium_explain": premium,
            "last_quiz_attempt_id": attempt_id,
        }
    )

    state["quiz_data"] = data
    state.pop("quiz_mode", None)
    user_states[chat_id] = state
    _save_telegram_quiz_state(tg_user_id, chat_id, state)

    verdict = "✅ Correct!" if passed else f"❌ Not correct. Correct answer: {correct}."
    short_explanation_line = f"\n\nWhy: {explain}" if explain else ""
    remaining = "Unlimited" if _quiz_is_paid(account_id) else str(max(0, QUIZ_FREE_DAILY_LIMIT - attempts))

    body = (
        "🧠 *Quiz Result*\n\n"
        f"{verdict}"
        f"{short_explanation_line}\n\n"
        f"Attempts today: {attempts}\n"
        f"Correct today: {correct_count}\n"
        f"Wrong today: {wrong_count}\n"
        f"Remaining today: {remaining}\n\n"
        "Reply Q1 for another quiz, Q2 for categories, Q3 for score, Q4 to review, Q5 for detailed saved explanation, or 0 for menu."
    )
    send_telegram_text(chat_id, _clip_text(body, 3900))
    return True


def _quiz_display_data(chat_id: str, tg_user_id: str = "") -> dict[str, Any]:
    persisted_state = _load_telegram_quiz_state(tg_user_id, chat_id) if _clean_text(tg_user_id) else {}
    if persisted_state:
        user_states[chat_id] = persisted_state
        data = persisted_state.get("quiz_data")
        return data if isinstance(data, dict) else {}
    return _quiz_data(chat_id)


def _send_quiz_score_telegram(chat_id: str, account_id: str, tg_user_id: str = "") -> None:
    data = _quiz_display_data(chat_id, tg_user_id)
    numbers = _quiz_daily_numbers_from_data(data)
    attempts = numbers["attempts"]
    accuracy = "0%" if attempts <= 0 else f"{round((numbers['correct'] / attempts) * 100)}%"
    remaining = "Unlimited" if _quiz_is_paid(account_id) else str(max(0, QUIZ_FREE_DAILY_LIMIT - attempts))

    body = (
        "📊 *Today's Quiz Score*\n\n"
        f"Attempts: {attempts}\n"
        f"Correct: {numbers['correct']}\n"
        f"Wrong: {numbers['wrong']}\n"
        f"Accuracy: {accuracy}\n"
        f"Remaining: {remaining}\n\n"
        "Reply Q1 to continue, Q2 to choose category, or 0 for menu."
    )
    send_telegram_text(chat_id, body)


def _send_quiz_review_telegram(chat_id: str, tg_user_id: str = "") -> None:
    data = _quiz_display_data(chat_id, tg_user_id)
    last = data.get("last_quiz") if isinstance(data.get("last_quiz"), dict) else None
    if not last:
        send_telegram_text(chat_id, "📌 No quiz answer to review yet. Reply Q1 to start a quiz.")
        return

    status = "✅ Correct" if last.get("is_correct") else "❌ Not correct"
    selected = _clean_text(last.get("selected"))
    correct = _clean_text(last.get("correct"))
    selected_text = _clean_text(last.get("selected_text"))
    correct_text = _clean_text(last.get("correct_text"))

    body = (
        "📌 *Last Quiz Review*\n\n"
        f"Category: {_clean_text(last.get('category')) or 'General'}\n"
        f"Question: {_clean_text(last.get('question'))}\n\n"
        f"Your answer: {selected}. {selected_text}\n"
        f"Correct answer: {correct}. {correct_text}\n"
        f"Status: {status}\n\n"
        f"Why: {_clean_text(last.get('explanation'))}\n\n"
        "Reply Q1 for another quiz or Q5 for the detailed saved explanation."
    )
    send_telegram_text(chat_id, _clip_text(body, 3900))


def _send_quiz_q5_telegram(chat_id: str, account_id: str, tg_user_id: str = "") -> None:
    data = _quiz_display_data(chat_id, tg_user_id)
    last = data.get("last_quiz") if isinstance(data.get("last_quiz"), dict) else None

    question = _clean_text((last or {}).get("question") or data.get("quiz_question"))
    explanation = _clean_text(
        (last or {}).get("premium_explanation")
        or data.get("last_quiz_premium_explain")
        or (last or {}).get("explanation")
        or data.get("last_quiz_explain")
    )
    attempt_id = _clean_text((last or {}).get("attempt_id") or data.get("last_quiz_attempt_id") or data.get("quiz_attempt_id"))

    if not question:
        send_telegram_text(chat_id, "No last quiz question found yet. Reply Q1 to start a quiz first.")
        return

    if not explanation:
        send_telegram_text(
            chat_id,
            "⚠️ A saved detailed explanation is not available for this quiz question yet.\n\n"
            "Reply Q4 for the normal review, Q1 for another quiz, or 0 for menu.",
        )
        return

    if not _quiz_is_paid(account_id):
        send_telegram_text(
            chat_id,
            "🔒 *Q5 Detailed Explanation is a paid feature*\n\n"
            "Q1-Q4 remain non-AI according to your plan limits.\n"
            "Q5 costs 1 Usage Credit because it unlocks the saved detailed explanation.\n"
            "It does not call live AI for fixed quiz content.\n\n"
            "Reply 4 to view plans or Q4 to review the normal explanation.",
        )
        return

    debit = _debit_q5_usage_credit_telegram(account_id, attempt_id)
    if not debit.get("ok"):
        if _clean_text(debit.get("error")) == "insufficient_credits":
            current_balance = int(debit.get("before") or 0)
            body = (
                "🔒 *Q5 Detailed Explanation locked*\n\n"
                f"Q5 costs 1 Usage Credit, but your current balance is {current_balance}.\n"
                "No detailed explanation was unlocked.\n\n"
                "Reply CR1 to check balance, 6 to buy add-ons, 4 to view plans, or Q4 for the normal review."
            )
        else:
            body = (
                "🔒 *Q5 Detailed Explanation not available*\n\n"
                "Q5 costs 1 Usage Credit, but your credit balance could not be charged.\n"
                "No detailed explanation was unlocked.\n\n"
                "Reply CR1 to check balance, 4 to view plans, or Q4 for the normal review."
            )
        send_telegram_text(chat_id, body)
        return

    _log_quiz_q5_used_telegram(attempt_id, debit)

    body = (
        "💡 *Q5 Detailed Explanation*\n\n"
        f"{_clip_text(explanation, 1200)}\n\n"
        "💎 Usage Credit deducted: 1\n"
        f"Balance: {debit.get('after')}\n\n"
        "Reply Q1 for another quiz, Q3 for score, or 0 for menu."
    )
    send_telegram_text(chat_id, _clip_text(body, 1800))


def _handle_quiz_command_telegram(chat_id: str, account_id: str, tg_user_id: str, text_raw: str) -> bool:
    norm = _quiz_norm(text_raw)

    if norm.startswith("q1") or norm in {"quiz", "start quiz", "tax quiz", "quiz me", "take quiz"}:
        category = _resolve_quiz_category_telegram(text_raw)
        _start_quiz_telegram(chat_id, account_id, tg_user_id, category)
        return True

    if norm.startswith("q2") or "category" in norm:
        category = _resolve_quiz_category_telegram(text_raw)
        if category:
            _start_quiz_telegram(chat_id, account_id, tg_user_id, category)
        else:
            send_telegram_text(chat_id, _quiz_category_menu_telegram())
        return True

    if norm.startswith("q3") or "score" in norm:
        _send_quiz_score_telegram(chat_id, account_id, tg_user_id)
        return True

    if norm.startswith("q4") or "review" in norm:
        _send_quiz_review_telegram(chat_id, tg_user_id)
        return True

    if norm.startswith("q5") or "explain" in norm:
        _send_quiz_q5_telegram(chat_id, account_id, tg_user_id)
        return True

    send_telegram_text(chat_id, _quiz_text_telegram())
    return True



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
        send_telegram_text(chat_id, _telegram_deadline_usage_text(account_id))
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
    if not isinstance(user_state, dict):
        user_state = {}

    # Batch 28H:
    # Restore quiz state from channel_identities.metadata more aggressively for
    # quiz-related messages. This prevents Q4/Q5 and A/B/C/D answers from using
    # stale in-memory worker state when Gunicorn routes messages across workers.
    quiz_text_candidate = bool(
        re.match(r"^Q[1-5]\b", text, flags=re.I)
        or _clean_text(text).upper() in {"A", "B", "C", "D"}
        or text_lower in {"quiz", "start quiz", "tax quiz", "quiz me", "take quiz", "cancel", "stop", "end"}
    )
    persisted_quiz_state = _load_telegram_quiz_state(tg_user_id, chat_id_str)
    if persisted_quiz_state and (
        not user_state
        or bool(user_state.get("quiz_mode"))
        or isinstance(user_state.get("quiz_data"), dict)
        or quiz_text_candidate
    ):
        user_state = persisted_quiz_state
        user_states[chat_id_str] = user_state

    has_subscription = bool(has_active_subscription(account_id))

    if not text:
        _send_welcome(chat_id_str, linked=linked)
        return jsonify({"ok": True})

    if text_lower in ["/start", "start", "0", "menu", "/menu"]:
        user_states.pop(chat_id_str, None)
        _clear_telegram_quiz_state(tg_user_id)
        _send_main_menu(chat_id_str, linked=linked)
        return jsonify({"ok": True})

    if text_lower in ["help", "/help", "?"]:
        _send_help(chat_id_str, linked=linked)
        return jsonify({"ok": True})

    if text_lower in ["back", "*", "cancel"]:
        if user_state:
            user_states.pop(chat_id_str, None)
            _clear_telegram_quiz_state(tg_user_id)
            send_telegram_text(chat_id_str, "Current flow cancelled.")
        _send_main_menu(chat_id_str, linked=linked)
        return jsonify({"ok": True})

    if text_lower in ["unlink", "unlink telegram", "disconnect telegram", "remove telegram"]:
        _clear_telegram_quiz_state(tg_user_id)
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

    # Quiz answer continuation must run before the master-command state clearing.
    if user_state.get("quiz_mode") == "answer":
        answer_norm = _clean_text(text).upper()
        if answer_norm in {"A", "B", "C", "D", "CANCEL", "STOP", "END"}:
            _handle_quiz_answer_telegram(chat_id_str, account_id, text, tg_user_id)
            return jsonify({"ok": True, "quiz_answer": True})

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
    # Preserve quiz memory for Q3/Q4/Q5, but clear stale state for unrelated commands.
    if not re.match(r"^Q[1-5]\b", text, flags=re.I):
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

    if user_state.get("calculator_type") and user_state.get("step"):
        _handle_calculator_step(chat_id_str, user_state, text)
        return jsonify({"ok": True, "calculator": True})

    if user_state.get("filing_type") and user_state.get("step"):
        _handle_continue_filing(chat_id_str, account_id, text)
        return jsonify({"ok": True})

    if _handle_tax_filing_command(chat_id_str, account_id, text):
        return jsonify({"ok": True})

    if text_lower in {"quiz", "tax quiz", "start quiz", "quiz me", "take quiz"}:
        _handle_quiz_command_telegram(chat_id_str, account_id, tg_user_id, text)
        return jsonify({"ok": True, "quiz": True})

    if MENU_NUMBER_RE.match(text):
        option = int(text)
        if option == 1:
            send_telegram_text(chat_id_str, "💬 Please type your Nigerian tax question.")
            return jsonify({"ok": True})
        if option == 2:
            send_telegram_text(chat_id_str, format_balance_message(get_credit_balance(account_id)))
            return jsonify({"ok": True})
        if option == 3:
            # Batch 30B: keep numeric current-plan display aligned with PAY1,
            # CR1, WhatsApp, and web workspace/ask pages.
            send_telegram_text(chat_id_str, _billing_summary_text(account_id))
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
        answer_payload = _handle_telegram_tax_question(
            chat_id=chat_id_str,
            account_id=account_id,
            tg_user_id=tg_user_id,
            question=text,
            account_source=_clean_text(resolved.get("source")),
        )
        return jsonify(answer_payload)
    except Exception as exc:
        logging.exception("Telegram AI ask flow error: %s", exc)
        send_telegram_text(
            chat_id_str,
            "Sorry, I encountered an error while answering your tax question. No credit should be charged for this failed request. Please try again later.\n\nReply 0 for main menu.",
        )
        return jsonify({"ok": True, "error_handled": True, "stage": "telegram_ai_ask"})
