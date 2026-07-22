# app/routes/telegram_expiry_patch.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from flask import Blueprint, jsonify

from app.routes import telegram as tg

bp = Blueprint("telegram_expiry_patch", __name__)

TELEGRAM_EXPIRY_PATCH_VERSION = "2026-07-22-v1-final-expired-plan-guard"
PAID_FAMILIES = {"starter", "professional", "business"}
EXPIRED_TOPUP_MESSAGE = (
    "⚠️ *Active paid plan required*\n\n"
    "Usage Credit add-ons are available only while a paid subscription is active. "
    "Your paid plan appears to be expired, so top-up checkout cannot start.\n\n"
    "Reply 4 to renew or choose a subscription plan."
)

_ORIGINAL_TG_GET_CREDIT_BALANCE = tg.get_credit_balance
_ORIGINAL_TG_CREATE_CREDIT_PAYMENT = tg.create_credit_payment
_ORIGINAL_TG_SEND_CREDIT_PACKAGE_MENU = getattr(tg, "_send_credit_package_menu", None)


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


def _is_active_paid(row: Optional[Dict[str, Any]]) -> bool:
    return bool(_is_active(row) and _is_paid_identity(row))


def _row_sort_dt(row: Dict[str, Any]) -> datetime:
    for key in ("current_period_end", "expires_at", "updated_at", "created_at", "paid_at"):
        dt = _safe_dt(row.get(key))
        if dt:
            return dt
    return datetime.min.replace(tzinfo=timezone.utc)


def _rows(resp: Any) -> list[Dict[str, Any]]:
    data = getattr(resp, "data", None)
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def _subscription_rows(account_id: str) -> list[Dict[str, Any]]:
    account_id = _clean(account_id)
    if not account_id:
        return []
    try:
        q = tg.supabase.table("user_subscriptions").select("*").eq("account_id", account_id)
        try:
            q = q.order("current_period_end", desc=True).order("updated_at", desc=True).order("created_at", desc=True)
        except Exception:
            pass
        return _rows(q.limit(50).execute())
    except Exception:
        return []


def _effective_subscription(account_id: str) -> Optional[Dict[str, Any]]:
    rows = _subscription_rows(account_id)
    if not rows:
        return None
    rows.sort(
        key=lambda row: (
            1 if _is_active_paid(row) else 0,
            1 if _is_active(row) else 0,
            1 if _is_paid_identity(row) else 0,
            _row_sort_dt(row),
        ),
        reverse=True,
    )
    return rows[0]


def _active_subscription(account_id: str) -> Optional[Dict[str, Any]]:
    row = _effective_subscription(account_id)
    return row if _is_active_paid(row) else None


def _expired_subscription(account_id: str) -> Optional[Dict[str, Any]]:
    row = _effective_subscription(account_id)
    return row if row and _is_paid_identity(row) and not _is_active_paid(row) else None


def _credit_value(balance: Any) -> int:
    try:
        if hasattr(tg, "_credit_balance_value"):
            return int(tg._credit_balance_value(balance))  # type: ignore[attr-defined]
    except Exception:
        pass
    if isinstance(balance, (int, float)):
        return int(balance)
    if isinstance(balance, dict):
        for key in ("balance", "credits", "credit_balance", "available_credits", "remaining_credits"):
            try:
                if balance.get(key) not in (None, ""):
                    return int(float(str(balance.get(key)).replace(",", "")))
            except Exception:
                continue
    return 0


def _patched_has_active_subscription(account_id: str) -> bool:
    return bool(_active_subscription(account_id))


def _patched_subscription_row(account_id: str) -> Optional[Dict[str, Any]]:
    return _active_subscription(account_id)


def _patched_get_credit_balance(account_id: str) -> int:
    if _expired_subscription(account_id):
        return 0
    try:
        return int(_ORIGINAL_TG_GET_CREDIT_BALANCE(account_id))
    except Exception:
        return 0


def _patched_create_credit_payment(account_id: str, package_num: int, channel_type: str, provider_user_id: str) -> Dict[str, Any]:
    if not _patched_has_active_subscription(account_id):
        return {"ok": False, "error": "active_paid_subscription_required", "message": EXPIRED_TOPUP_MESSAGE}
    return _ORIGINAL_TG_CREATE_CREDIT_PAYMENT(account_id, package_num, channel_type, provider_user_id)


def _active_plan_name(sub: Dict[str, Any]) -> str:
    code = _clean(sub.get("plan_code") or sub.get("plan") or "")
    try:
        from app.services import channel_subscription_service as css
        plan = css.validate_plan_code(code)  # type: ignore[attr-defined]
        if isinstance(plan, dict) and plan.get("full_name"):
            return _clean(plan.get("full_name"))
    except Exception:
        pass
    return _clean(sub.get("plan_name") or sub.get("name") or code.replace("_", " ").title() or "Paid plan")


def _billing_summary_text(account_id: str) -> str:
    active = _active_subscription(account_id)
    expired = _expired_subscription(account_id)

    if active:
        balance = _credit_value(_patched_get_credit_balance(account_id))
        plan_name = _active_plan_name(active)
        status = _clean(active.get("status") or "active")
        expiry = _expiry_value(active)
        body = (
            "💳 *Billing Summary*\n\n"
            f"Plan: {plan_name}\n"
            f"Status: {status}\n"
            f"Usage Credits: {balance}\n"
        )
        if expiry:
            body += f"Renewal/expiry: {_date_label(expiry)}\n"
        body += "\nReply PAY2 for payment history, PAY6 for renewal/expiry, or 0 for main menu."
        return body

    if expired:
        previous = _clean(expired.get("plan_code") or expired.get("plan_name") or "previous paid plan")
        return (
            "💳 *Billing Summary*\n\n"
            f"Plan: {previous} (expired)\n"
            "Status: expired\n"
            "Usage Credits: 0\n"
            f"Expired: {_date_label(_expiry_value(expired))}\n\n"
            "Paid access is not active. Renew before using paid AI answers, top-ups, custom deadlines, or paid workspace features.\n\n"
            "Reply 4 to view subscription plans or 0 for main menu."
        )

    balance = _credit_value(_ORIGINAL_TG_GET_CREDIT_BALANCE(account_id))
    return (
        "💳 *Billing Summary*\n\n"
        "Current plan: Free Forever\n"
        f"Usage Credits: {balance}\n"
        "Status: Free access\n\n"
        "Reply 4 to view subscription plans, PAY2 for payment history, or 0 for main menu."
    )


def _format_subscription_message(account_id: str) -> str:
    active = _active_subscription(account_id)
    expired = _expired_subscription(account_id)
    if expired and not active:
        previous = _clean(expired.get("plan_code") or expired.get("plan_name") or "previous paid plan")
        return (
            "📋 *NO ACTIVE SUBSCRIPTION*\n\n"
            f"Previous plan: {previous}\n"
            f"Expired: {_date_label(_expiry_value(expired))}\n"
            "Status: expired\n\n"
            "Renew before using paid AI answers, top-ups, custom deadlines, or paid workspace features.\n\n"
            "Reply with 4 to see available plans and renew."
        )
    if active:
        return _billing_summary_text(account_id).replace("💳 *Billing Summary*", "📋 *YOUR SUBSCRIPTION*")
    return (
        "📋 *NO ACTIVE SUBSCRIPTION*\n\n"
        "You are currently on free access.\n"
        "Reply with 4 to see available plans and upgrade."
    )


def _send_renewal_expiry(chat_id: str, account_id: str) -> None:
    active = _active_subscription(account_id)
    expired = _expired_subscription(account_id)
    if active:
        tg.send_telegram_text(
            chat_id,
            "📅 *Renewal / Expiry Date*\n\n"
            f"Plan: {_active_plan_name(active)}\n"
            f"Renewal/expiry: {_date_label(_expiry_value(active))}\n\n"
            "Reply PAY1 for billing summary or PAY2 for payment history.",
        )
        return
    if expired:
        previous = _clean(expired.get("plan_code") or expired.get("plan_name") or "previous paid plan")
        tg.send_telegram_text(
            chat_id,
            "📅 *Renewal / Expiry Date*\n\n"
            f"Plan: {previous}\n"
            "Status: expired\n"
            f"Expired: {_date_label(_expiry_value(expired))}\n\n"
            "Reply 4 to renew or choose a subscription plan.",
        )
        return
    tg.send_telegram_text(chat_id, "📅 *Renewal / Expiry Date*\n\nNo active paid subscription found.\n\nReply 4 to view subscription plans.")


def _send_credit_package_menu(chat_id: str, account_id: str, *, has_subscription: bool) -> None:
    if not _patched_has_active_subscription(account_id):
        tg.send_telegram_text(chat_id, EXPIRED_TOPUP_MESSAGE)
        return
    if callable(_ORIGINAL_TG_SEND_CREDIT_PACKAGE_MENU):
        return _ORIGINAL_TG_SEND_CREDIT_PACKAGE_MENU(chat_id, account_id, has_subscription=True)
    tg.send_telegram_text(chat_id, tg._topup_menu_text())  # type: ignore[attr-defined]


def apply_patch() -> None:
    tg.has_active_subscription = _patched_has_active_subscription  # type: ignore[assignment]
    tg.get_credit_balance = _patched_get_credit_balance  # type: ignore[assignment]
    tg.create_credit_payment = _patched_create_credit_payment  # type: ignore[assignment]
    tg.format_subscription_message = _format_subscription_message  # type: ignore[assignment]
    tg._subscription_row = _patched_subscription_row  # type: ignore[attr-defined]
    tg._billing_summary_text = _billing_summary_text  # type: ignore[attr-defined]
    tg._send_renewal_expiry = _send_renewal_expiry  # type: ignore[attr-defined]
    tg._send_credit_package_menu = _send_credit_package_menu  # type: ignore[attr-defined]

    try:
        from app.services import channel_subscription_service as css
        css.has_active_subscription = _patched_has_active_subscription  # type: ignore[attr-defined]
        css.get_user_subscription = _patched_subscription_row  # type: ignore[attr-defined]
        css.format_subscription_message = _format_subscription_message  # type: ignore[attr-defined]
    except Exception:
        pass

    try:
        from app.services import channel_credit_service as ccs
        ccs.get_credit_balance = _patched_get_credit_balance  # type: ignore[attr-defined]
        ccs.create_credit_payment = _patched_create_credit_payment  # type: ignore[attr-defined]
    except Exception:
        pass


apply_patch()


@bp.get("/telegram/expiry-patch/health")
def telegram_expiry_patch_health():
    return jsonify({"ok": True, "version": TELEGRAM_EXPIRY_PATCH_VERSION})
