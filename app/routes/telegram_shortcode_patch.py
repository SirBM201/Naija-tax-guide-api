# app/routes/telegram_shortcode_patch.py
from __future__ import annotations

import re
import uuid
from typing import Any, Optional

from flask import Blueprint, jsonify

from app.routes import telegram as tg

bp = Blueprint("telegram_shortcode_patch", __name__)

TELEGRAM_SHORTCODE_PATCH_VERSION = "2026-06-14-v3-shortcodes-and-uuid-safe-fallback"
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.I)


def _clean(value: Any) -> str:
    try:
        return tg._clean_text(value)  # type: ignore[attr-defined]
    except Exception:
        return str(value or "").strip()


def _is_uuid(value: Any) -> bool:
    return bool(UUID_RE.match(_clean(value)))


def _fallback_uuid_for_tg_user_id(provider_user_id: str) -> str:
    provider_user_id = _clean(provider_user_id)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"naija-tax-guide:telegram:{provider_user_id}"))


def _normalize_shortcode_text(value: Any) -> str:
    raw = _clean(value)
    if not raw:
        return ""

    parts = raw.split(maxsplit=1)
    first = parts[0].strip()
    rest = parts[1].strip() if len(parts) > 1 else ""

    if first.startswith("/"):
        first = first[1:]
        if "@" in first:
            first = first.split("@", 1)[0]

    return f"{first} {rest}".strip()


def _normalize_lower(value: Any) -> str:
    return _normalize_shortcode_text(value).lower()


_ORIGINAL_HANDLE_MASTER_COMMAND = tg._handle_master_command
_ORIGINAL_LOOKS_LIKE_BAD_COMMAND = tg._looks_like_bad_command
_ORIGINAL_SELECT_CREDIT_PACKAGE_NUMBER = tg._select_credit_package_number
_ORIGINAL_RESOLVE_TELEGRAM_ACCOUNT = tg._resolve_telegram_account
_ORIGINAL_EFFECTIVE_ACCOUNT_ID_FROM_TG_ACCOUNT = tg._effective_account_id_from_tg_account
_ORIGINAL_HAS_ACTIVE_SUBSCRIPTION = tg.has_active_subscription


def _best_uuid_from_tg_account_row(row: Optional[dict[str, Any]]) -> str:
    if not isinstance(row, dict):
        return ""

    # auth_user_id is the linked website workspace UUID. If absent, id is the
    # standalone Telegram shell account UUID. Do not return provider_user_id or
    # numeric account_id values because subscription/credit tables expect UUIDs.
    for key in ("auth_user_id", "account_id", "id"):
        value = _clean(row.get(key))
        if _is_uuid(value):
            return value
    return ""


def _patched_effective_account_id_from_tg_account(row: Optional[dict[str, Any]]) -> Optional[str]:
    uuid_value = _best_uuid_from_tg_account_row(row)
    if uuid_value:
        return uuid_value
    return None


def _telegram_account_row(provider_user_id: str) -> Optional[dict[str, Any]]:
    provider_user_id = _clean(provider_user_id)
    if not provider_user_id:
        return None

    try:
        row = tg._get_telegram_account_row(provider_user_id)  # type: ignore[attr-defined]
        if isinstance(row, dict) and row:
            return row
    except Exception:
        pass

    try:
        resp = (
            tg.supabase.table("accounts")  # type: ignore[attr-defined]
            .select("id,account_id,provider,provider_user_id,auth_user_id,display_name,email,updated_at,created_at")
            .eq("provider", "tg")
            .eq("provider_user_id", provider_user_id)
            .limit(1)
            .execute()
        )
        data = getattr(resp, "data", None)
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return data[0]
        if isinstance(data, dict):
            return data
    except Exception:
        return None

    return None


def _repair_identity_account_id(identity: Optional[dict[str, Any]], account_id: str, display_name: Optional[str] = None) -> None:
    if not isinstance(identity, dict) or not _is_uuid(account_id):
        return

    identity_id = _clean(identity.get("id"))
    if not identity_id:
        return

    metadata = identity.get("metadata") if isinstance(identity.get("metadata"), dict) else {}
    if display_name and not metadata.get("display_name"):
        metadata = {**metadata, "display_name": display_name}

    payloads: list[dict[str, Any]] = [
        {"account_id": account_id, "metadata": metadata, "updated_at": tg._utc_now_iso()},  # type: ignore[attr-defined]
        {"account_id": account_id, "updated_at": tg._utc_now_iso()},  # type: ignore[attr-defined]
        {"account_id": account_id},
    ]

    for payload in payloads:
        try:
            tg.supabase.table("channel_identities").update(payload).eq("id", identity_id).execute()  # type: ignore[attr-defined]
            return
        except Exception:
            continue


def _ensure_uuid_safe_tg_shell(provider_user_id: str, display_name: Optional[str] = None, identity: Optional[dict[str, Any]] = None) -> str:
    """
    Last-resort repair path.

    Telegram must not return System error just because legacy rows contain a raw
    Telegram numeric ID. This creates/uses a deterministic valid UUID for the
    Telegram shell account, then patches account/channel rows where possible.
    Paid website access still depends on a clean website link/auth_user_id, but
    free Telegram commands and quiz should continue instead of crashing.
    """
    provider_user_id = _clean(provider_user_id)
    fallback_uuid = _fallback_uuid_for_tg_user_id(provider_user_id)
    now = tg._utc_now_iso()  # type: ignore[attr-defined]

    # Try to patch the existing Telegram provider row with a UUID-safe account_id.
    row = _telegram_account_row(provider_user_id)
    if isinstance(row, dict) and row:
        row_id = _clean(row.get("id"))
        payloads = [
            {"account_id": fallback_uuid, "updated_at": now, "display_name": display_name},
            {"account_id": fallback_uuid, "updated_at": now},
            {"account_id": fallback_uuid},
        ]
        for payload in payloads:
            payload = {k: v for k, v in payload.items() if v not in (None, "")}
            try:
                if row_id:
                    tg.supabase.table("accounts").update(payload).eq("id", row_id).execute()  # type: ignore[attr-defined]
                    break
                tg.supabase.table("accounts").update(payload).eq("provider", "tg").eq("provider_user_id", provider_user_id).execute()  # type: ignore[attr-defined]
                break
            except Exception:
                continue
    else:
        # If no Telegram row exists, try a minimal insert/upsert. Column-safe by retries.
        payloads = [
            {
                "account_id": fallback_uuid,
                "provider": "tg",
                "provider_user_id": provider_user_id,
                "display_name": display_name,
                "created_at": now,
                "updated_at": now,
            },
            {
                "account_id": fallback_uuid,
                "provider": "tg",
                "provider_user_id": provider_user_id,
                "updated_at": now,
            },
            {
                "provider": "tg",
                "provider_user_id": provider_user_id,
                "account_id": fallback_uuid,
            },
        ]
        for payload in payloads:
            payload = {k: v for k, v in payload.items() if v not in (None, "")}
            try:
                tg.supabase.table("accounts").upsert(payload, on_conflict="provider,provider_user_id").execute()  # type: ignore[attr-defined]
                break
            except Exception:
                continue

    if isinstance(identity, dict) and identity:
        _repair_identity_account_id(identity, fallback_uuid, display_name=display_name)

    return fallback_uuid


def _patched_has_active_subscription(account_id: str) -> bool:
    account_id = _clean(account_id)
    if not account_id or not _is_uuid(account_id):
        return False
    try:
        return bool(_ORIGINAL_HAS_ACTIVE_SUBSCRIPTION(account_id))
    except Exception:
        return False


def _patched_resolve_telegram_account(*, tg_user_id: str, display_name: Optional[str] = None) -> dict[str, Any]:
    tg_user_id = _clean(tg_user_id)
    if not tg_user_id:
        return {"ok": False, "reason": "missing_tg_user_id"}

    identity = None
    try:
        identity = tg._get_telegram_identity(tg_user_id)  # type: ignore[attr-defined]
    except Exception:
        identity = None

    row = _telegram_account_row(tg_user_id)
    row_uuid = _best_uuid_from_tg_account_row(row)

    if isinstance(identity, dict) and identity:
        identity_account_id = _clean(identity.get("account_id"))
        if _is_uuid(identity_account_id):
            try:
                tg._touch_telegram_identity(identity, display_name=display_name)  # type: ignore[attr-defined]
            except Exception:
                pass
            return {
                "ok": True,
                "account_id": identity_account_id,
                "linked": True,
                "identity": identity,
                "source": "channel_identities_uuid",
            }

        if row_uuid:
            _repair_identity_account_id(identity, row_uuid, display_name=display_name)
            try:
                tg._touch_telegram_identity(identity, display_name=display_name)  # type: ignore[attr-defined]
            except Exception:
                pass
            return {
                "ok": True,
                "account_id": row_uuid,
                "linked": bool(_clean((row or {}).get("auth_user_id"))),
                "identity": {**identity, "account_id": row_uuid},
                "source": "repaired_channel_identity_uuid",
            }

    try:
        resolved = _ORIGINAL_RESOLVE_TELEGRAM_ACCOUNT(tg_user_id=tg_user_id, display_name=display_name)
    except Exception as exc:
        resolved = {"ok": False, "reason": "original_resolver_failed", "error": str(exc)}

    resolved_account_id = _clean((resolved or {}).get("account_id"))
    if _is_uuid(resolved_account_id):
        return resolved

    row = _telegram_account_row(tg_user_id)
    row_uuid = _best_uuid_from_tg_account_row(row)
    if row_uuid:
        return {
            "ok": True,
            "account_id": row_uuid,
            "linked": bool(_clean((row or {}).get("auth_user_id"))),
            "identity": (resolved or {}).get("identity"),
            "source": "accounts_uuid_patch",
            "previous_account_id": resolved_account_id,
        }

    fallback_uuid = _ensure_uuid_safe_tg_shell(tg_user_id, display_name=display_name, identity=identity)
    return {
        "ok": True,
        "account_id": fallback_uuid,
        "linked": False,
        "identity": identity,
        "source": "uuid_safe_telegram_shell_fallback",
        "previous_account_id": resolved_account_id,
    }


def _quiz_state_for(chat_id: str, tg_user_id: str) -> dict[str, Any]:
    state: dict[str, Any] = {}
    try:
        if _clean(tg_user_id):
            loaded = tg._load_telegram_quiz_state(tg_user_id, chat_id)  # type: ignore[attr-defined]
            if isinstance(loaded, dict):
                state = loaded
    except Exception:
        state = {}

    if not state:
        try:
            maybe_state = tg.user_states.get(chat_id, {})  # type: ignore[attr-defined]
            if isinstance(maybe_state, dict):
                state = maybe_state
        except Exception:
            state = {}

    return state


def _patched_handle_master_command(
    *,
    chat_id: str,
    account_id: str,
    tg_user_id: str,
    text_raw: str,
    linked: bool,
    has_subscription: bool,
) -> bool:
    normalized = _normalize_shortcode_text(text_raw)
    normalized_upper = normalized.upper()
    normalized_lower = normalized.lower()

    if normalized_upper in {"A", "B", "C", "D", "CANCEL", "STOP", "END"}:
        state = _quiz_state_for(chat_id, tg_user_id)
        if state.get("quiz_mode") == "answer":
            tg._handle_quiz_answer_telegram(chat_id, account_id, normalized, tg_user_id)  # type: ignore[attr-defined]
            return True

    if normalized_lower in {"menu", "start"}:
        tg.user_states.pop(chat_id, None)  # type: ignore[attr-defined]
        try:
            tg._clear_telegram_quiz_state(tg_user_id)  # type: ignore[attr-defined]
        except Exception:
            pass
        tg._send_main_menu(chat_id, linked=linked)  # type: ignore[attr-defined]
        return True

    if normalized_lower in {"help", "?"}:
        tg._send_help(chat_id, linked=linked)  # type: ignore[attr-defined]
        return True

    if normalized_lower in {"back", "cancel"}:
        tg.user_states.pop(chat_id, None)  # type: ignore[attr-defined]
        try:
            tg._clear_telegram_quiz_state(tg_user_id)  # type: ignore[attr-defined]
        except Exception:
            pass
        tg.send_telegram_text(chat_id, "Current flow cancelled.\n\nReply 0 or MENU for the main menu.")
        return True

    return _ORIGINAL_HANDLE_MASTER_COMMAND(
        chat_id=chat_id,
        account_id=account_id,
        tg_user_id=tg_user_id,
        text_raw=normalized,
        linked=linked,
        has_subscription=has_subscription,
    )


def _patched_select_credit_package_number(text_lower: str) -> Optional[int]:
    return _ORIGINAL_SELECT_CREDIT_PACKAGE_NUMBER(_normalize_lower(text_lower))


def _patched_looks_like_bad_command(text: str) -> bool:
    return _ORIGINAL_LOOKS_LIKE_BAD_COMMAND(_normalize_shortcode_text(text))


def _patched_invalid_command_text(value: str = "") -> str:
    shown = f"\n\nReceived: {value}" if value else ""
    return (
        "⚠️ That menu code is not available, so no AI credit was used."
        f"{shown}\n\n"
        "Useful commands:\n"
        "0 - Main menu\n"
        "ALL or /all - Full command list\n"
        "S1/P1/B1 - Subscription plans\n"
        "T10/T50/T100/T500 - Credit add-ons\n"
        "PAY1 - Billing summary\n"
        "PAY2 - Payment history\n"
        "CR1 - Credit balance\n"
        "CR2 - Recent credit activity\n"
        "Q1 - Tax quiz\n"
        "H1 - Recent tax history"
    )


def _patched_send_credit_activity(chat_id: str, account_id: str) -> None:
    rows = tg._combined_credit_activity_rows(account_id, mode="ai", limit=5)  # type: ignore[attr-defined]
    balance = tg.get_credit_balance(account_id)

    if not rows:
        bal = tg._credit_balance_value(balance) if isinstance(balance, dict) else "Not shown"  # type: ignore[attr-defined]
        tg.send_telegram_text(
            chat_id,
            "*📉 Usage Credit Activity*\n\n"
            "No recent credit deduction log found yet.\n\n"
            f"Current balance: {bal}\n\n"
            "Reply CR1 for balance, CR4 for top-up/addition history, 6 to buy add-ons, or 0 for main menu.",
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
        msg += f"   Date: {tg._date_short(created_at)}\n\n"  # type: ignore[attr-defined]

    msg += "Reply CR1 for balance, CR3 for AI deductions, CR4 for additions/top-ups, 6 to buy add-ons, or 0 for main menu."
    tg.send_telegram_text(chat_id, msg)


def _patched_send_credit_rules(chat_id: str) -> None:
    tg.send_telegram_text(
        chat_id,
        "*💎 Usage Credit Rules*\n\n"
        "• Credits are shared across web, WhatsApp, and Telegram when your channels are linked.\n"
        "• AI tax answers and premium quiz explanations may deduct credits.\n"
        "• Basic calculators and free tools should remain available according to your plan rules.\n"
        "• Add-ons are available only to active paid subscribers.\n\n"
        "Reply CR1 for balance, CR2 for recent credit activity, CR4 for additions/top-ups, or 6 to buy add-ons.",
    )


def _patched_send_payment_history(chat_id: str, account_id: str) -> None:
    rows: list[dict[str, Any]] = []
    for table_name in ("paystack_transactions", "payment_transactions", "billing_transactions"):
        rows = tg._safe_table_rows(table_name, account_id, limit=5)  # type: ignore[attr-defined]
        if rows:
            break

    if not rows:
        tg.send_telegram_text(
            chat_id,
            "*🧾 Payment History*\n\n"
            "No payment history found for this account yet.\n\n"
            "Reply 4 to view subscription plans, PAY1 for billing summary, or PAY6 for billing support.",
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
            msg += f"   Amount: {tg._money(amount)}\n"  # type: ignore[attr-defined]
        msg += f"   Status: {status}\n"
        if reference:
            msg += f"   Ref: {reference}\n"
        msg += f"   Date: {tg._date_short(created_at)}\n\n"  # type: ignore[attr-defined]

    msg += "Reply PAY1 for billing summary, PAY2 for payment history, PAY3 for latest payment, 4 for plans, or 0 for main menu."
    tg.send_telegram_text(chat_id, msg)


def _patched_send_upgrade_help(chat_id: str) -> None:
    tg.send_telegram_text(
        chat_id,
        "*🛒 Upgrade / Renew Help*\n\n"
        "1. Reply 4 to view available plans.\n"
        "2. Choose a plan using S1, S2, S3, P1, P2, P3, B1, B2, or B3.\n"
        "3. Complete payment through the secure checkout link.\n"
        "4. Your web, WhatsApp, and Telegram access should update automatically after payment.\n\n"
        "Reply PAY1 to check your current plan, PAY2 for payment history, or PAY6 for billing support.",
    )


def _patched_send_renewal_help(chat_id: str) -> None:
    tg.send_telegram_text(
        chat_id,
        "*🔁 Renewal / Cancel Information*\n\n"
        "Your current plan details are shown with PAY1.\n\n"
        "To upgrade or renew, reply 4 and select a plan code such as P1 or B1.\n"
        "To review payment history, reply PAY2.\n"
        "To cancel or resolve billing issues, contact support.\n\n"
        "Support: support@naijataxguides.com",
    )


def _patched_send_billing_support(chat_id: str) -> None:
    tg.send_telegram_text(
        chat_id,
        "*🧾 Billing Support*\n\n"
        "For failed payment, wrong plan, missing credits, or subscription issues, contact:\n"
        "support@naijataxguides.com\n\n"
        "Include your registered email/phone and payment reference if available.\n\n"
        "Reply PAY2 for payment history, PAY4 <reference> to verify a payment reference, or 0 for main menu.",
    )


def _patched_send_all_commands(chat_id: str, *, linked: bool = False) -> None:
    msg = (
        "📋 *Naija Tax Guide Command List*\n\n"
        "Telegram accepts plain short codes like Q1, CR1, PAY1 and slash forms like /q1, /cr1, /pay1.\n\n"
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
    tg.send_telegram_text(chat_id, msg)


def apply_patch() -> None:
    tg._effective_account_id_from_tg_account = _patched_effective_account_id_from_tg_account  # type: ignore[assignment]
    tg._resolve_telegram_account = _patched_resolve_telegram_account  # type: ignore[assignment]
    tg.has_active_subscription = _patched_has_active_subscription  # type: ignore[assignment]
    tg._handle_master_command = _patched_handle_master_command  # type: ignore[assignment]
    tg._select_credit_package_number = _patched_select_credit_package_number  # type: ignore[assignment]
    tg._looks_like_bad_command = _patched_looks_like_bad_command  # type: ignore[assignment]
    tg._invalid_command_text = _patched_invalid_command_text  # type: ignore[assignment]
    tg._send_credit_activity = _patched_send_credit_activity  # type: ignore[assignment]
    tg._send_credit_rules = _patched_send_credit_rules  # type: ignore[assignment]
    tg._send_payment_history = _patched_send_payment_history  # type: ignore[assignment]
    tg._send_upgrade_help = _patched_send_upgrade_help  # type: ignore[assignment]
    tg._send_renewal_help = _patched_send_renewal_help  # type: ignore[assignment]
    tg._send_billing_support = _patched_send_billing_support  # type: ignore[assignment]
    tg._send_all_commands = _patched_send_all_commands  # type: ignore[assignment]


apply_patch()


@bp.get("/telegram/shortcode-patch/health")
def telegram_shortcode_patch_health():
    return jsonify({"ok": True, "version": TELEGRAM_SHORTCODE_PATCH_VERSION})
