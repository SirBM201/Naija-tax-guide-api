# app/routes/telegram_expiry_patch.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from flask import Blueprint, jsonify

from app.routes import telegram as tg

bp = Blueprint("telegram_expiry_patch", __name__)

TELEGRAM_EXPIRY_PATCH_VERSION = "2026-07-23-v2-expired-plan-copy-and-guard"
PAID_FAMILIES = {"starter", "professional", "business"}
BLOCKED_AI_GUIDANCE = (
    "\n\nNo credit was charged for this blocked request. "
    "Reply 4 to renew or choose a paid plan. Reply CR1 to check your credit balance."
)
EXPIRED_TOPUP_MESSAGE = (
    "⚠️ *Active paid plan required*\n\n"
    "Usage Credit add-ons are available only while a paid subscription is active. "
    "Your paid plan appears to be expired, so top-up checkout cannot start.\n\n"
    "Reply 4 to renew or choose a subscription plan."
)

_ORIGINAL_SUBSCRIPTION_ROW = getattr(tg, "_subscription_row", None)
_ORIGINAL_BILLING_SUMMARY_TEXT = getattr(tg, "_billing_summary_text", None)
_ORIGINAL_RENEWAL_EXPIRY = getattr(tg, "_send_renewal_expiry", None)
_ORIGINAL_GET_CREDIT_BALANCE = getattr(tg, "get_credit_balance", None)
_ORIGINAL_CREATE_CREDIT_PAYMENT = getattr(tg, "create_credit_payment", None)
_ORIGINAL_SEND_CREDIT_PACKAGE_MENU = getattr(tg, "_send_credit_package_menu", None)
_ORIGINAL_TELEGRAM_ANSWER_CREDIT_NOTE = getattr(tg, "_telegram_answer_credit_note", None)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _lower(value: Any) -> str:
    return _clean(value).lower()


def _safe_dt(value: Any) -> Optional[datetime]:
    raw = _clean(value)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _expiry_value(row: Optional[Dict[str, Any]]) -> Any:
    if not row:
        return None
    return (
        row.get("current_period_end")
        or row.get("expires_at")
        or row.get("valid_until")
        or row.get("ends_at")
        or row.get("period_end")
        or row.get("grace_until")
        or row.get("trial_until")
    )


def _date_label(value: Any) -> str:
    dt = _safe_dt(value)
    if dt:
        return dt.strftime("%Y-%m-%d")
    raw = _clean(value)
    return raw[:10] if raw else "not shown"


def _is_expired(row: Optional[Dict[str, Any]]) -> bool:
    expiry = _safe_dt(_expiry_value(row))
    return bool(expiry and expiry <= _now())


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on", "active", "paid", "enabled"}:
        return True
    if text in {"0", "false", "no", "n", "off", "inactive", "expired", "disabled"}:
        return False
    return default


def _plan_family(value: Any) -> str:
    text = _lower(value)
    if "business" in text:
        return "business"
    if "professional" in text or text.startswith("pro") or "pro_" in text:
        return "professional"
    if "starter" in text:
        return "starter"
    return "free"


def _is_paid_identity(row: Optional[Dict[str, Any]]) -> bool:
    if not row:
        return False
    code = row.get("plan_code") or row.get("plan") or row.get("tier") or row.get("plan_name")
    return _plan_family(code) in PAID_FAMILIES


def _is_active(row: Optional[Dict[str, Any]]) -> bool:
    if not row or _is_expired(row):
        return False
    status = _lower(row.get("status") or row.get("subscription_status") or row.get("payment_status"))
    if status in {"inactive", "expired", "cancelled", "canceled", "disabled", "paused", "failed"}:
        return False
    explicit = row.get("is_active")
    if explicit is not None and not _truthy(explicit, default=True):
        return False
    expiry = _safe_dt(_expiry_value(row))
    if expiry is not None:
        return expiry > _now()
    return status in {"active", "paid", "trial", "trialing", "grace", "past_due", "successful", "success"}


def _row_sort_dt(row: Dict[str, Any]) -> datetime:
    for key in ("current_period_end", "expires_at", "updated_at", "created_at", "paid_at"):
        dt = _safe_dt(row.get(key))
        if dt:
            return dt
    return datetime.min.replace(tzinfo=timezone.utc)


def _subscription_rows(account_id: str) -> list[Dict[str, Any]]:
    account_id = _clean(account_id)
    if not account_id:
        return []
    try:
        query = tg.supabase.table("user_subscriptions").select("*").eq("account_id", account_id)  # type: ignore[attr-defined]
        try:
            query = query.order("current_period_end", desc=True).order("updated_at", desc=True).order("created_at", desc=True)
        except Exception:
            pass
        response = query.limit(50).execute()
        data = getattr(response, "data", None) or []
        return [row for row in data if isinstance(row, dict)]
    except Exception:
        return []


def _effective_subscription(account_id: str) -> Optional[Dict[str, Any]]:
    rows = _subscription_rows(account_id)
    if not rows:
        if callable(_ORIGINAL_SUBSCRIPTION_ROW):
            try:
                row = _ORIGINAL_SUBSCRIPTION_ROW(account_id)
                return dict(row) if isinstance(row, dict) else None
            except Exception:
                return None
        return None
    rows.sort(
        key=lambda row: (
            1 if _is_active(row) and _is_paid_identity(row) else 0,
            1 if _is_active(row) else 0,
            1 if _is_paid_identity(row) else 0,
            _row_sort_dt(row),
        ),
        reverse=True,
    )
    return dict(rows[0])


def _active_subscription(account_id: str) -> Optional[Dict[str, Any]]:
    row = _effective_subscription(account_id)
    return row if row and _is_active(row) and _is_paid_identity(row) else None


def _expired_paid_subscription(account_id: str) -> Optional[Dict[str, Any]]:
    row = _effective_subscription(account_id)
    return row if row and _is_paid_identity(row) and not (_is_active(row) and not _is_expired(row)) else None


def _credit_balance_value(balance: Any) -> int:
    if isinstance(balance, (int, float)):
        return int(balance)
    if isinstance(balance, dict):
        for key in ("balance", "credits", "credit_balance", "available_credits", "remaining_credits", "usage_credits"):
            if balance.get(key) not in (None, ""):
                try:
                    return int(float(str(balance.get(key)).replace(",", "")))
                except Exception:
                    continue
    return 0


def patched_subscription_row(account_id: str) -> Optional[Dict[str, Any]]:
    return _active_subscription(account_id)


def patched_has_active_subscription(account_id: str) -> bool:
    return bool(_active_subscription(account_id))


def patched_get_credit_balance(account_id: str) -> int:
    if _expired_paid_subscription(account_id):
        return 0
    if callable(_ORIGINAL_GET_CREDIT_BALANCE):
        return _credit_balance_value(_ORIGINAL_GET_CREDIT_BALANCE(account_id))
    return 0


def patched_create_credit_payment(account_id: str, package_num: int, channel_type: str, provider_user_id: str) -> Dict[str, Any]:
    if not patched_has_active_subscription(account_id):
        return {"ok": False, "error": "active_paid_subscription_required", "message": EXPIRED_TOPUP_MESSAGE}
    if callable(_ORIGINAL_CREATE_CREDIT_PAYMENT):
        return _ORIGINAL_CREATE_CREDIT_PAYMENT(account_id, package_num, channel_type, provider_user_id)
    return {"ok": False, "error": "credit_payment_unavailable", "message": "Payment checkout is not available right now."}


def patched_billing_summary_text(account_id: str) -> str:
    active = _active_subscription(account_id)
    expired = _expired_paid_subscription(account_id)

    if active and callable(_ORIGINAL_BILLING_SUMMARY_TEXT):
        try:
            return _ORIGINAL_BILLING_SUMMARY_TEXT(account_id)
        except Exception:
            pass

    if expired:
        plan = _clean(expired.get("plan_name") or expired.get("plan_code") or "previous paid plan")
        expiry = _date_label(_expiry_value(expired))
        return (
            "💳 *Billing Summary*\n\n"
            f"Plan: {plan} (expired)\n"
            "Status: expired\n"
            "Usage Credits: 0\n"
            f"Expired: {expiry}\n\n"
            "Paid access is not active. Renew before using paid AI answers, top-ups, custom deadlines, or paid workspace features.\n\n"
            "Reply 4 to view subscription plans or 0 for main menu."
        )

    if callable(_ORIGINAL_BILLING_SUMMARY_TEXT):
        try:
            return _ORIGINAL_BILLING_SUMMARY_TEXT(account_id)
        except Exception:
            pass

    return (
        "💳 *Billing Summary*\n\n"
        "Current plan: Free Forever\n"
        "Usage Credits: 0\n"
        "Status: Free access\n\n"
        "Reply 4 to view subscription plans or 0 for main menu."
    )


def patched_send_renewal_expiry(chat_id: str, account_id: str) -> None:
    active = _active_subscription(account_id)
    expired = _expired_paid_subscription(account_id)
    row = active or expired
    if not row:
        tg.send_telegram_text(chat_id, "📅 *Renewal / Expiry Date*\n\nNo active paid subscription found.\n\nReply 4 to view subscription plans.")  # type: ignore[attr-defined]
        return

    plan = _clean(row.get("plan_name") or row.get("plan_code") or "Current plan")
    expiry = _date_label(_expiry_value(row))
    status = "active" if active else "expired"
    tg.send_telegram_text(  # type: ignore[attr-defined]
        chat_id,
        "📅 *Renewal / Expiry Date*\n\n"
        f"Plan: {plan}\n"
        f"Status: {status}\n"
        f"Renewal/expiry: {expiry}\n\n"
        + ("Reply PAY1 for billing summary or 0 for main menu." if active else "Reply 4 to renew or 0 for main menu."),
    )


def patched_send_credit_package_menu(chat_id: str, account_id: str, *, has_subscription: bool) -> None:
    if not patched_has_active_subscription(account_id):
        tg.send_telegram_text(chat_id, EXPIRED_TOPUP_MESSAGE)  # type: ignore[attr-defined]
        return
    if callable(_ORIGINAL_SEND_CREDIT_PACKAGE_MENU):
        return _ORIGINAL_SEND_CREDIT_PACKAGE_MENU(chat_id, account_id, has_subscription=True)
    tg.send_telegram_text(chat_id, "Reply T10, T50, T100, or T500 to buy Usage Credit add-ons.")  # type: ignore[attr-defined]


def patched_telegram_answer_credit_note(result: Dict[str, Any]) -> str:
    if not isinstance(result, dict):
        return ""
    result_ok = bool(result.get("ok") is True)
    error_code = _clean(result.get("error"))
    if not result_ok and error_code in {"paid_plan_required", "insufficient_credits", "no_credits", "credit_balance_empty"}:
        return BLOCKED_AI_GUIDANCE
    if callable(_ORIGINAL_TELEGRAM_ANSWER_CREDIT_NOTE):
        try:
            return _ORIGINAL_TELEGRAM_ANSWER_CREDIT_NOTE(result)
        except Exception:
            return ""
    return ""


def apply_patch() -> None:
    tg._subscription_row = patched_subscription_row  # type: ignore[attr-defined]
    tg.has_active_subscription = patched_has_active_subscription  # type: ignore[attr-defined]
    tg.get_credit_balance = patched_get_credit_balance  # type: ignore[attr-defined]
    tg.create_credit_payment = patched_create_credit_payment  # type: ignore[attr-defined]
    tg._billing_summary_text = patched_billing_summary_text  # type: ignore[attr-defined]
    tg._send_renewal_expiry = patched_send_renewal_expiry  # type: ignore[attr-defined]
    tg._send_credit_package_menu = patched_send_credit_package_menu  # type: ignore[attr-defined]
    tg._telegram_answer_credit_note = patched_telegram_answer_credit_note  # type: ignore[attr-defined]
    tg._ntg_telegram_expiry_patch_applied = TELEGRAM_EXPIRY_PATCH_VERSION  # type: ignore[attr-defined]


apply_patch()


@bp.get("/telegram/expiry-patch/health")
def telegram_expiry_patch_health():
    return jsonify({"ok": True, "version": TELEGRAM_EXPIRY_PATCH_VERSION})
