# app/routes/telegram_shortcode_patch.py
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from flask import Blueprint, jsonify

from app.routes import telegram as tg

bp = Blueprint("telegram_shortcode_patch", __name__)

TELEGRAM_SHORTCODE_PATCH_VERSION = "2026-06-14-v4-shortcodes-quiz-answer-guard"
TELEGRAM_QUIZ_STATE_METADATA_KEY = "telegram_quiz_state_v1"
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.I)
ANSWER_CODES = {"A", "B", "C", "D"}
CANCEL_CODES = {"CANCEL", "STOP", "END"}


def _clean(value: Any) -> str:
    try:
        return tg._clean_text(value)  # type: ignore[attr-defined]
    except Exception:
        return str(value or "").strip()


def _is_uuid(value: Any) -> bool:
    return bool(UUID_RE.match(_clean(value)))


def _rows(resp: Any) -> list[dict[str, Any]]:
    data = getattr(resp, "data", None)
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def _first(resp: Any) -> Optional[dict[str, Any]]:
    rows = _rows(resp)
    return rows[0] if rows else None


def _fallback_uuid_for_tg_user_id(provider_user_id: str) -> str:
    provider_user_id = _clean(provider_user_id)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"naija-tax-guide:telegram:{provider_user_id}"))


def _now_iso() -> str:
    try:
        return tg._utc_now_iso()  # type: ignore[attr-defined]
    except Exception:
        return datetime.now(timezone.utc).isoformat()


def _parse_dt(value: Any) -> Optional[datetime]:
    text = _clean(value)
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _fresh_dt(value: Any, seconds: int = 3600) -> bool:
    dt = _parse_dt(value)
    if not dt:
        return False
    try:
        return (datetime.now(timezone.utc) - dt).total_seconds() <= seconds
    except Exception:
        return False


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
_ORIGINAL_LOAD_TELEGRAM_QUIZ_STATE = tg._load_telegram_quiz_state
_ORIGINAL_SAVE_TELEGRAM_QUIZ_STATE = tg._save_telegram_quiz_state
_ORIGINAL_CLEAR_TELEGRAM_QUIZ_STATE = tg._clear_telegram_quiz_state


def _best_uuid_from_tg_account_row(row: Optional[dict[str, Any]]) -> str:
    if not isinstance(row, dict):
        return ""
    for key in ("auth_user_id", "account_id", "id"):
        value = _clean(row.get(key))
        if _is_uuid(value):
            return value
    return ""


def _patched_effective_account_id_from_tg_account(row: Optional[dict[str, Any]]) -> Optional[str]:
    return _best_uuid_from_tg_account_row(row) or None


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
        return _first(
            tg.supabase.table("accounts")  # type: ignore[attr-defined]
            .select("id,account_id,provider,provider_user_id,auth_user_id,display_name,email,updated_at,created_at")
            .eq("provider", "tg")
            .eq("provider_user_id", provider_user_id)
            .limit(1)
            .execute()
        )
    except Exception:
        return None


def _identity_rows(provider_user_id: str) -> list[dict[str, Any]]:
    provider_user_id = _clean(provider_user_id)
    if not provider_user_id:
        return []
    out: list[dict[str, Any]] = []
    for channel_type in ("telegram", "tg"):
        try:
            rows = _rows(
                tg.supabase.table("channel_identities")  # type: ignore[attr-defined]
                .select("*")
                .eq("channel_type", channel_type)
                .eq("provider_user_id", provider_user_id)
                .limit(5)
                .execute()
            )
            out.extend(rows)
        except Exception:
            continue
    # De-duplicate by id/account/type.
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for row in out:
        key = _clean(row.get("id") or f"{row.get('channel_type')}:{row.get('provider_user_id')}:{row.get('account_id')}")
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _best_identity(provider_user_id: str) -> Optional[dict[str, Any]]:
    rows = _identity_rows(provider_user_id)
    if not rows:
        return None
    # Prefer rows with real website UUIDs and metadata.
    rows.sort(key=lambda r: (1 if _is_uuid(r.get("account_id")) else 0, 1 if isinstance(r.get("metadata"), dict) else 0, _clean(r.get("updated_at") or r.get("last_seen_at") or r.get("created_at"))), reverse=True)
    return rows[0]


def _repair_identity_account_id(identity: Optional[dict[str, Any]], account_id: str, display_name: Optional[str] = None) -> None:
    if not isinstance(identity, dict) or not _is_uuid(account_id):
        return
    metadata = identity.get("metadata") if isinstance(identity.get("metadata"), dict) else {}
    if display_name and not metadata.get("display_name"):
        metadata = {**metadata, "display_name": display_name}
    payloads = [
        {"account_id": account_id, "metadata": metadata, "updated_at": _now_iso()},
        {"account_id": account_id, "metadata": metadata},
        {"account_id": account_id},
    ]
    identity_id = _clean(identity.get("id"))
    for payload in payloads:
        try:
            if identity_id:
                tg.supabase.table("channel_identities").update(payload).eq("id", identity_id).execute()  # type: ignore[attr-defined]
            else:
                tg.supabase.table("channel_identities").update(payload).eq("channel_type", _clean(identity.get("channel_type") or "telegram")).eq("provider_user_id", _clean(identity.get("provider_user_id"))).execute()  # type: ignore[attr-defined]
            return
        except Exception:
            continue


def _ensure_uuid_safe_tg_shell(provider_user_id: str, display_name: Optional[str] = None, identity: Optional[dict[str, Any]] = None) -> str:
    provider_user_id = _clean(provider_user_id)
    fallback_uuid = _fallback_uuid_for_tg_user_id(provider_user_id)
    now = _now_iso()
    row = _telegram_account_row(provider_user_id)
    if isinstance(row, dict) and row:
        row_id = _clean(row.get("id"))
        for payload in (
            {"account_id": fallback_uuid, "updated_at": now, "display_name": display_name},
            {"account_id": fallback_uuid, "updated_at": now},
            {"account_id": fallback_uuid},
        ):
            payload = {k: v for k, v in payload.items() if v not in (None, "")}
            try:
                if row_id:
                    tg.supabase.table("accounts").update(payload).eq("id", row_id).execute()  # type: ignore[attr-defined]
                else:
                    tg.supabase.table("accounts").update(payload).eq("provider", "tg").eq("provider_user_id", provider_user_id).execute()  # type: ignore[attr-defined]
                break
            except Exception:
                continue
    else:
        for payload in (
            {"account_id": fallback_uuid, "provider": "tg", "provider_user_id": provider_user_id, "display_name": display_name, "created_at": now, "updated_at": now},
            {"account_id": fallback_uuid, "provider": "tg", "provider_user_id": provider_user_id, "updated_at": now},
            {"provider": "tg", "provider_user_id": provider_user_id, "account_id": fallback_uuid},
        ):
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
    if not _is_uuid(account_id):
        return False
    try:
        return bool(_ORIGINAL_HAS_ACTIVE_SUBSCRIPTION(account_id))
    except Exception:
        return False


def _patched_resolve_telegram_account(*, tg_user_id: str, display_name: Optional[str] = None) -> dict[str, Any]:
    tg_user_id = _clean(tg_user_id)
    if not tg_user_id:
        return {"ok": False, "reason": "missing_tg_user_id"}
    identity = _best_identity(tg_user_id)
    row = _telegram_account_row(tg_user_id)
    row_uuid = _best_uuid_from_tg_account_row(row)
    if isinstance(identity, dict) and identity:
        identity_account_id = _clean(identity.get("account_id"))
        if _is_uuid(identity_account_id):
            try:
                tg._touch_telegram_identity(identity, display_name=display_name)  # type: ignore[attr-defined]
            except Exception:
                pass
            return {"ok": True, "account_id": identity_account_id, "linked": True, "identity": identity, "source": "channel_identities_uuid"}
        if row_uuid:
            _repair_identity_account_id(identity, row_uuid, display_name=display_name)
            return {"ok": True, "account_id": row_uuid, "linked": bool(_clean((row or {}).get("auth_user_id"))), "identity": {**identity, "account_id": row_uuid}, "source": "repaired_channel_identity_uuid"}
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
        return {"ok": True, "account_id": row_uuid, "linked": bool(_clean((row or {}).get("auth_user_id"))), "identity": (resolved or {}).get("identity"), "source": "accounts_uuid_patch", "previous_account_id": resolved_account_id}
    fallback_uuid = _ensure_uuid_safe_tg_shell(tg_user_id, display_name=display_name, identity=identity)
    return {"ok": True, "account_id": fallback_uuid, "linked": False, "identity": identity, "source": "uuid_safe_telegram_shell_fallback", "previous_account_id": resolved_account_id}


def _compact_quiz_state(chat_id: str, state: dict[str, Any]) -> dict[str, Any]:
    data = state.get("quiz_data") if isinstance(state.get("quiz_data"), dict) else {}
    return {"chat_id": _clean(chat_id), "quiz_mode": _clean(state.get("quiz_mode")), "quiz_data": data, "saved_at": _now_iso(), "version": "shortcode-patch-v4"}


def _state_from_identity(identity: dict[str, Any], chat_id: str = "") -> dict[str, Any]:
    metadata = identity.get("metadata") if isinstance(identity.get("metadata"), dict) else {}
    saved = metadata.get(TELEGRAM_QUIZ_STATE_METADATA_KEY)
    if not isinstance(saved, dict):
        return {}
    if not _fresh_dt(saved.get("saved_at"), seconds=86400):
        return {}
    saved_chat_id = _clean(saved.get("chat_id"))
    if chat_id and saved_chat_id and saved_chat_id != _clean(chat_id):
        return {}
    data = saved.get("quiz_data") if isinstance(saved.get("quiz_data"), dict) else {}
    if not data:
        return {}
    return {"quiz_mode": _clean(saved.get("quiz_mode")), "quiz_data": data}


def _patched_load_telegram_quiz_state(tg_user_id: str, chat_id: str = "") -> dict[str, Any]:
    try:
        state = _ORIGINAL_LOAD_TELEGRAM_QUIZ_STATE(tg_user_id, chat_id)
        if isinstance(state, dict) and state.get("quiz_data"):
            return state
    except Exception:
        pass
    for identity in _identity_rows(tg_user_id):
        state = _state_from_identity(identity, chat_id)
        if state:
            return state
    return {}


def _patched_save_telegram_quiz_state(tg_user_id: str, chat_id: str, state: dict[str, Any]) -> None:
    try:
        _ORIGINAL_SAVE_TELEGRAM_QUIZ_STATE(tg_user_id, chat_id, state)
    except Exception:
        pass
    tg_user_id = _clean(tg_user_id)
    if not tg_user_id:
        return
    compact = _compact_quiz_state(chat_id, state)
    identities = _identity_rows(tg_user_id)
    if not identities:
        row = _telegram_account_row(tg_user_id)
        account_id = _best_uuid_from_tg_account_row(row) or _fallback_uuid_for_tg_user_id(tg_user_id)
        try:
            tg.supabase.table("channel_identities").insert({"account_id": account_id, "channel_type": "telegram", "provider_user_id": tg_user_id, "metadata": {TELEGRAM_QUIZ_STATE_METADATA_KEY: compact}}).execute()  # type: ignore[attr-defined]
            return
        except Exception:
            pass
        identities = _identity_rows(tg_user_id)
    for identity in identities:
        metadata = identity.get("metadata") if isinstance(identity.get("metadata"), dict) else {}
        metadata = {**metadata, TELEGRAM_QUIZ_STATE_METADATA_KEY: compact}
        identity_id = _clean(identity.get("id"))
        for payload in ({"metadata": metadata}, {"metadata": metadata, "updated_at": _now_iso()}):
            try:
                if identity_id:
                    tg.supabase.table("channel_identities").update(payload).eq("id", identity_id).execute()  # type: ignore[attr-defined]
                else:
                    tg.supabase.table("channel_identities").update(payload).eq("channel_type", _clean(identity.get("channel_type") or "telegram")).eq("provider_user_id", tg_user_id).execute()  # type: ignore[attr-defined]
                break
            except Exception:
                continue


def _patched_clear_telegram_quiz_state(tg_user_id: str) -> None:
    try:
        _ORIGINAL_CLEAR_TELEGRAM_QUIZ_STATE(tg_user_id)
    except Exception:
        pass
    for identity in _identity_rows(tg_user_id):
        metadata = identity.get("metadata") if isinstance(identity.get("metadata"), dict) else {}
        if TELEGRAM_QUIZ_STATE_METADATA_KEY not in metadata:
            continue
        metadata.pop(TELEGRAM_QUIZ_STATE_METADATA_KEY, None)
        identity_id = _clean(identity.get("id"))
        try:
            if identity_id:
                tg.supabase.table("channel_identities").update({"metadata": metadata}).eq("id", identity_id).execute()  # type: ignore[attr-defined]
        except Exception:
            continue


def _quiz_state_for(chat_id: str, tg_user_id: str) -> dict[str, Any]:
    state: dict[str, Any] = {}
    try:
        state = tg._load_telegram_quiz_state(tg_user_id, chat_id)  # type: ignore[attr-defined]
    except Exception:
        state = {}
    if not state:
        try:
            maybe_state = tg.user_states.get(chat_id, {})  # type: ignore[attr-defined]
            if isinstance(maybe_state, dict):
                state = maybe_state
        except Exception:
            state = {}
    return state if isinstance(state, dict) else {}


def _latest_pending_quiz_attempt(account_id: str) -> Optional[dict[str, Any]]:
    account_id = _clean(account_id)
    if not _is_uuid(account_id):
        return None
    for select_cols in ("id,account_id,question_code,category,status,created_at,channel", "id,account_id,question_code,category,status,created_at"):
        try:
            row = _first(
                tg.supabase.table("tax_quiz_attempts")  # type: ignore[attr-defined]
                .select(select_cols)
                .eq("account_id", account_id)
                .eq("status", "started")
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            if row and _fresh_dt(row.get("created_at"), seconds=1800):
                return row
        except Exception:
            continue
    return None


def _handle_or_guard_quiz_answer(chat_id: str, account_id: str, tg_user_id: str, answer: str) -> bool:
    answer = _clean(answer).upper()[:1]
    state = _quiz_state_for(chat_id, tg_user_id)
    if state.get("quiz_mode") == "answer":
        tg._handle_quiz_answer_telegram(chat_id, account_id, answer, tg_user_id)  # type: ignore[attr-defined]
        return True

    pending = _latest_pending_quiz_attempt(account_id)
    if pending:
        try:
            tg.supabase.table("tax_quiz_attempts").update({"status": "expired", "updated_at": _now_iso()}).eq("id", _clean(pending.get("id"))).execute()  # type: ignore[attr-defined]
        except Exception:
            pass
        tg.send_telegram_text(
            chat_id,
            f"I received *{answer}* as a quiz answer, but the quiz session could not be restored.\n\n"
            "No AI credit was used for that letter. Please reply Q1 to start a fresh quiz question.",
        )
        return True

    tg.send_telegram_text(chat_id, f"*{answer}* is treated as a quiz option only during an active quiz.\n\nReply Q1 to start a quiz, or 0 for the main menu. No AI credit was used.")
    return True


def _patched_handle_master_command(*, chat_id: str, account_id: str, tg_user_id: str, text_raw: str, linked: bool, has_subscription: bool) -> bool:
    normalized = _normalize_shortcode_text(text_raw)
    normalized_upper = normalized.upper()
    normalized_lower = normalized.lower()

    if normalized_upper in ANSWER_CODES:
        return _handle_or_guard_quiz_answer(chat_id, account_id, tg_user_id, normalized_upper)

    if normalized_upper in CANCEL_CODES:
        state = _quiz_state_for(chat_id, tg_user_id)
        if state.get("quiz_mode") == "answer":
            tg._handle_quiz_answer_telegram(chat_id, account_id, normalized_upper, tg_user_id)  # type: ignore[attr-defined]
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

    return _ORIGINAL_HANDLE_MASTER_COMMAND(chat_id=chat_id, account_id=account_id, tg_user_id=tg_user_id, text_raw=normalized, linked=linked, has_subscription=has_subscription)


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
        tg.send_telegram_text(chat_id, "*📉 Usage Credit Activity*\n\nNo recent credit deduction log found yet.\n\n" f"Current balance: {bal}\n\n" "Reply CR1 for balance, CR4 for top-up/addition history, 6 to buy add-ons, or 0 for main menu.")
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
    tg.send_telegram_text(chat_id, "*💎 Usage Credit Rules*\n\n• Credits are shared across web, WhatsApp, and Telegram when your channels are linked.\n• AI tax answers and premium quiz explanations may deduct credits.\n• Basic calculators and free tools should remain available according to your plan rules.\n• Add-ons are available only to active paid subscribers.\n\nReply CR1 for balance, CR2 for recent credit activity, CR4 for additions/top-ups, or 6 to buy add-ons.")


def _patched_send_payment_history(chat_id: str, account_id: str) -> None:
    rows: list[dict[str, Any]] = []
    for table_name in ("paystack_transactions", "payment_transactions", "billing_transactions"):
        rows = tg._safe_table_rows(table_name, account_id, limit=5)  # type: ignore[attr-defined]
        if rows:
            break
    if not rows:
        tg.send_telegram_text(chat_id, "*🧾 Payment History*\n\nNo payment history found for this account yet.\n\nReply 4 to view subscription plans, PAY1 for billing summary, or PAY6 for billing support.")
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
    tg.send_telegram_text(chat_id, "*🛒 Upgrade / Renew Help*\n\n1. Reply 4 to view available plans.\n2. Choose a plan using S1, S2, S3, P1, P2, P3, B1, B2, or B3.\n3. Complete payment through the secure checkout link.\n4. Your web, WhatsApp, and Telegram access should update automatically after payment.\n\nReply PAY1 to check your current plan, PAY2 for payment history, or PAY6 for billing support.")


def _patched_send_renewal_help(chat_id: str) -> None:
    tg.send_telegram_text(chat_id, "*🔁 Renewal / Cancel Information*\n\nYour current plan details are shown with PAY1.\n\nTo upgrade or renew, reply 4 and select a plan code such as P1 or B1.\nTo review payment history, reply PAY2.\nTo cancel or resolve billing issues, contact support.\n\nSupport: support@naijataxguides.com")


def _patched_send_billing_support(chat_id: str) -> None:
    tg.send_telegram_text(chat_id, "*🧾 Billing Support*\n\nFor failed payment, wrong plan, missing credits, or subscription issues, contact:\nsupport@naijataxguides.com\n\nInclude your registered email/phone and payment reference if available.\n\nReply PAY2 for payment history, PAY4 <reference> to verify a payment reference, or 0 for main menu.")


def _patched_send_all_commands(chat_id: str, *, linked: bool = False) -> None:
    msg = (
        "📋 *Naija Tax Guide Command List*\n\n"
        "Telegram accepts plain short codes like Q1, CR1, PAY1 and slash forms like /q1, /cr1, /pay1.\n\n"
        "Main menu:\n1 - Ask a tax question\n2 - Check Usage Credits\n3 - Check current plan\n4 - View subscription plans\n5 - Link/unlink website account\n6 - Buy Usage Credit add-ons\n7 - Tax tools, filing & quiz\n8 - Help\n\n"
        "Plans:\nS1/S2/S3 - Starter monthly/quarterly/yearly\nP1/P2/P3 - Professional monthly/quarterly/yearly\nB1/B2/B3 - Business monthly/quarterly/yearly\n\n"
        "Credits and billing:\nT10/T50/T100/T500 - Buy credit add-ons\nCR1 - Credit balance\nCR2 - Recent credit activity\nCR3 - AI credit deductions\nCR4 - Credit additions/top-ups\nPAY1 - Billing summary\nPAY2 - Payment history\nPAY3 - Latest payment status\nPAY4 <reference> - Verify payment reference\nPAY5 - Pending plan change\nPAY6 - Renewal/expiry date\n\n"
        "Tax tools and quiz:\nF1 - Calculator menu\nF2 - PAYE filing guide\nF3 - VAT filing guide\nF4 - CIT filing guide\nF5 - WHT guide\nF6 - Tax deadlines/calendar\nF7 - Filing checklist\nF8 - Back to main menu\nC1 - PAYE calculator\nC2 - Company Income Tax calculator\nC3 - VAT calculator\nC4 - Withholding Tax calculator\nC5 - Salary/net pay comparison\nC6 or Q1 - Tax quiz\nC7 - Tax calendar/deadlines\nC8 - Back to Tax Tools\nQ2 - Quiz categories\nQ3 - Quiz score\nQ4 - Last quiz review\nQ5 - Detailed saved quiz explanation\n\n"
        "Deadlines and history:\nD1 - Create reminder\nD2 - List reminders\nD3 - Delete reminder\nD4 - Update reminder\nH1 - Recent tax history\nH2 - Last tax answer\n\n"
        "Support, referral, filing, account:\nSUP1-SUP6 - Support tickets and support email\nR1-R6 - Referral code, link, stats, rewards, payout\nFT1-FT8 - Filing assistance and filing requests\nACC1-ACC3 - Account/profile and linked channels\nSET1-SET3 - Settings guidance\n\n"
        "Navigation:\n0 or MENU - Main menu\n* or BACK - Go back\nCANCEL - Cancel current flow"
    )
    tg.send_telegram_text(chat_id, msg)


def apply_patch() -> None:
    tg._effective_account_id_from_tg_account = _patched_effective_account_id_from_tg_account  # type: ignore[assignment]
    tg._resolve_telegram_account = _patched_resolve_telegram_account  # type: ignore[assignment]
    tg.has_active_subscription = _patched_has_active_subscription  # type: ignore[assignment]
    tg._load_telegram_quiz_state = _patched_load_telegram_quiz_state  # type: ignore[assignment]
    tg._save_telegram_quiz_state = _patched_save_telegram_quiz_state  # type: ignore[assignment]
    tg._clear_telegram_quiz_state = _patched_clear_telegram_quiz_state  # type: ignore[assignment]
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
