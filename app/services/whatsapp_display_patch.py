from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional


PAID_FAMILIES = {"starter", "professional", "business"}
EXPIRED_TOPUP_MESSAGE = (
    "⚠️ *Active paid plan required*\n\n"
    "Usage Credit add-ons are available only while a paid subscription is active. "
    "Your paid plan appears to be expired, so top-up checkout cannot start.\n\n"
    "Reply 4 to renew or choose a subscription plan."
)
EXPIRED_PLAN_MESSAGE = (
    "📌 *Current Plan*\n\n"
    "No active paid subscription is currently available on this account.\n"
    "If your previous paid plan has expired, please renew before using paid AI answers, top-ups, custom deadlines, or paid workspace features.\n\n"
    "Reply 4 to view subscription plans or 0 for main menu."
)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _lower(value: Any) -> str:
    return _clean(value).lower()


def _safe_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    raw = str(value).strip().lower()
    if raw in {"1", "true", "yes", "y", "on", "active", "paid", "enabled"}:
        return True
    if raw in {"0", "false", "no", "n", "off", "inactive", "expired", "disabled"}:
        return False
    return default


def _plan_family_from_code(value: Any) -> str:
    text = _lower(value)
    if "business" in text:
        return "business"
    if "professional" in text or text.startswith("pro") or "pro_" in text:
        return "professional"
    if "starter" in text:
        return "starter"
    return "free"


def _subscription_expiry_value(row: Optional[Dict[str, Any]]) -> Any:
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


def _is_expired(row: Optional[Dict[str, Any]]) -> bool:
    expiry = _safe_dt(_subscription_expiry_value(row))
    return bool(expiry and expiry <= _now_utc())


def _is_subscription_active(row: Optional[Dict[str, Any]]) -> bool:
    if not row:
        return False
    if _is_expired(row):
        return False

    status = _lower(row.get("status") or row.get("subscription_status") or row.get("payment_status"))
    if status in {"inactive", "expired", "cancelled", "canceled", "disabled", "paused", "failed"}:
        return False

    explicit = row.get("is_active")
    if explicit is not None and not _truthy(explicit, default=True):
        return False

    expiry = _safe_dt(_subscription_expiry_value(row))
    if expiry is not None:
        return expiry > _now_utc()

    return status in {"active", "paid", "trial", "trialing", "grace", "past_due", "successful", "success"}


def _is_active_paid_subscription(row: Optional[Dict[str, Any]]) -> bool:
    if not _is_subscription_active(row):
        return False
    code = _lower((row or {}).get("plan_code") or (row or {}).get("plan") or (row or {}).get("tier"))
    return _plan_family_from_code(code) in PAID_FAMILIES


def _is_paid_subscription_identity(row: Optional[Dict[str, Any]]) -> bool:
    code = _lower((row or {}).get("plan_code") or (row or {}).get("plan") or (row or {}).get("tier"))
    return _plan_family_from_code(code) in PAID_FAMILIES


def _row_sort_dt(row: Dict[str, Any]) -> datetime:
    for key in ("current_period_end", "expires_at", "updated_at", "created_at", "paid_at"):
        dt = _safe_dt(row.get(key))
        if dt:
            return dt
    return datetime.min.replace(tzinfo=timezone.utc)


def _select_effective_subscription(rows: list[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    clean_rows = [r for r in rows if isinstance(r, dict)]
    if not clean_rows:
        return None

    ranked = sorted(
        clean_rows,
        key=lambda row: (
            1 if _is_active_paid_subscription(row) else 0,
            1 if _is_subscription_active(row) else 0,
            1 if _is_paid_subscription_identity(row) else 0,
            _row_sort_dt(row),
        ),
        reverse=True,
    )
    return ranked[0]


def _normalize_subscription_row(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not row:
        return None
    out = dict(row)
    active = _is_subscription_active(out)
    expired = _is_expired(out)

    out["active"] = active
    out["is_active"] = active
    if expired:
        out["status"] = "expired"
        out["expired"] = True
        out["expired_plan_code"] = out.get("plan_code")
        out["expired_plan_name"] = out.get("plan_name") or out.get("name")
    elif not active and _is_paid_subscription_identity(out):
        raw_status = _lower(out.get("status"))
        out["status"] = raw_status if raw_status in {"inactive", "cancelled", "canceled", "disabled", "paused", "failed"} else "inactive"
        out["expired"] = False
    else:
        out["status"] = _lower(out.get("status")) or ("active" if active else "inactive")
        out["expired"] = False
    return out


def _expired_payload(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "expired": True,
        "status": "expired",
        "active": False,
        "is_active": False,
        "plan_code": "expired",
        "plan_family": "expired",
        "plan_name": "Expired Plan",
        "expired_plan_code": (row or {}).get("plan_code"),
        "expired_plan_name": (row or {}).get("plan_name") or (row or {}).get("name"),
    }


def _date_label(value: Any) -> str:
    dt = _safe_dt(value)
    if dt:
        return dt.strftime("%Y-%m-%d")
    raw = _clean(value)
    return raw[:10] if raw else "not shown"


def _channel_db_clients():
    clients = []
    for module_name in (
        "app.services.channel_subscription_service",
        "app.routes.whatsapp",
        "app.routes.telegram",
    ):
        try:
            module = __import__(module_name, fromlist=["*"])
            sb_func = getattr(module, "_sb", None)
            if callable(sb_func):
                clients.append(sb_func())
        except Exception:
            pass
    try:
        from app.core.supabase_client import supabase

        clients.append(supabase() if callable(supabase) else supabase)
    except Exception:
        pass
    return [client for client in clients if client is not None]


def _subscription_rows_for_account(account_id: str) -> list[Dict[str, Any]]:
    account_id = _clean(account_id)
    if not account_id:
        return []

    for client in _channel_db_clients():
        try:
            q = client.table("user_subscriptions").select("*").eq("account_id", account_id)
            try:
                q = q.order("current_period_end", desc=True).order("updated_at", desc=True).order("created_at", desc=True)
            except Exception:
                pass
            res = q.limit(50).execute()
            rows = [r for r in (getattr(res, "data", None) or []) if isinstance(r, dict)]
            if rows:
                return rows
        except Exception:
            continue
    return []


def _effective_subscription_for_account(account_id: str) -> Optional[Dict[str, Any]]:
    return _normalize_subscription_row(_select_effective_subscription(_subscription_rows_for_account(account_id)))


def _active_subscription_for_account(account_id: str) -> Optional[Dict[str, Any]]:
    row = _effective_subscription_for_account(account_id)
    return row if _is_active_paid_subscription(row) else None


def _expired_paid_subscription_for_account(account_id: str) -> Optional[Dict[str, Any]]:
    row = _effective_subscription_for_account(account_id)
    return row if row and _is_paid_subscription_identity(row) and not _is_active_paid_subscription(row) else None


def _apply_topup_pricing_patch() -> None:
    try:
        from app.routes import billing
    except Exception:
        return

    restored_packages: Dict[str, Dict[str, Any]] = {
        "TOPUP_10": {"code": "TOPUP_10", "name": "10 Usage Credits", "description": "Add 10 AI/usage credits to an active paid account.", "credits": 10, "amount_ngn": 500, "amount_kobo": 500 * 100, "currency": "NGN", "paid_plan_required": True},
        "TOPUP_50": {"code": "TOPUP_50", "name": "50 Usage Credits", "description": "Add 50 AI/usage credits to an active paid account.", "credits": 50, "amount_ngn": 2000, "amount_kobo": 2000 * 100, "currency": "NGN", "paid_plan_required": True},
        "TOPUP_100": {"code": "TOPUP_100", "name": "100 Usage Credits", "description": "Add 100 AI/usage credits to an active paid account.", "credits": 100, "amount_ngn": 3500, "amount_kobo": 3500 * 100, "currency": "NGN", "paid_plan_required": True},
        "TOPUP_500": {"code": "TOPUP_500", "name": "500 Usage Credits", "description": "Add 500 AI/usage credits to an active paid account.", "credits": 500, "amount_ngn": 15000, "amount_kobo": 15000 * 100, "currency": "NGN", "paid_plan_required": True},
    }

    aliases = {
        "10": "TOPUP_10", "50": "TOPUP_50", "100": "TOPUP_100", "500": "TOPUP_500",
        "T10": "TOPUP_10", "T50": "TOPUP_50", "T100": "TOPUP_100", "T500": "TOPUP_500",
        "TOPUP10": "TOPUP_10", "TOPUP50": "TOPUP_50", "TOPUP100": "TOPUP_100", "TOPUP500": "TOPUP_500",
        "TOPUP_10": "TOPUP_10", "TOPUP_50": "TOPUP_50", "TOPUP_100": "TOPUP_100", "TOPUP_500": "TOPUP_500",
    }

    try:
        billing.TOPUP_PACKAGES.clear()
        billing.TOPUP_PACKAGES.update(restored_packages)
    except Exception:
        billing.TOPUP_PACKAGES = restored_packages  # type: ignore[attr-defined]

    try:
        billing.TOPUP_CODE_ALIASES.clear()
        billing.TOPUP_CODE_ALIASES.update(aliases)
    except Exception:
        billing.TOPUP_CODE_ALIASES = aliases  # type: ignore[attr-defined]


def _apply_subscription_expiry_patch() -> None:
    try:
        from app.routes import billing
    except Exception:
        return

    if getattr(billing, "_ntg_expiry_payload_patch_applied", False):
        return

    original_is_active = getattr(billing, "_subscription_is_active", None)
    original_payload = getattr(billing, "_subscription_payload", None)

    def billing_is_expired(row: Optional[Dict[str, Any]]) -> bool:
        try:
            raw = billing._subscription_expiry(row)  # type: ignore[attr-defined]
        except Exception:
            raw = _subscription_expiry_value(row)
        try:
            expiry = billing._parse_dt(raw)  # type: ignore[attr-defined]
        except Exception:
            expiry = _safe_dt(raw)
        return bool(expiry and expiry <= _now_utc())

    if callable(original_is_active):
        def patched_subscription_is_active(row: Optional[Dict[str, Any]]) -> bool:
            if billing_is_expired(row):
                return False
            return bool(original_is_active(row))

        billing._subscription_is_active = patched_subscription_is_active  # type: ignore[attr-defined]

    if callable(original_payload):
        def patched_subscription_payload(account_id: str, sub: Optional[Dict[str, Any]], account: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
            payload = original_payload(account_id, sub, account)
            if not sub:
                return payload

            normalized = _normalize_subscription_row(sub) or {}
            active = bool(normalized.get("active"))
            expired = bool(normalized.get("expired"))
            status = _clean(normalized.get("status") or ("active" if active else "inactive"))
            original_plan_code = _clean(payload.get("plan_code") or sub.get("plan_code"))
            original_plan_name = _clean(payload.get("plan_name") or sub.get("plan_name") or sub.get("name"))

            payload["active"] = active
            payload["is_active"] = active
            payload["status"] = status
            payload["expired"] = expired

            if expired:
                payload.update(_expired_payload({**sub, "plan_name": original_plan_name}))
                payload["expired_plan_code"] = original_plan_code or None
                payload["expired_plan_name"] = original_plan_name or None
                payload["included_credits"] = 0
                payload["credit_balance"] = 0
                payload["topup_allowed"] = False
                payload["topup_eligibility_reason"] = "subscription_expired"

            subscription = payload.get("subscription")
            if isinstance(subscription, dict):
                subscription.update(normalized)
                if expired:
                    subscription.update(_expired_payload({**subscription, "plan_name": original_plan_name}))
                    subscription["included_credits"] = 0

            summary = payload.get("subscription_summary")
            if isinstance(summary, dict):
                summary["is_active_now"] = active
                summary["status"] = status
                if expired:
                    summary["current_plan_code"] = "expired"

            return payload

        billing._subscription_payload = patched_subscription_payload  # type: ignore[attr-defined]

    try:
        from app.services import subscription_guard

        original_build_access = getattr(subscription_guard, "_build_access", None)
        if callable(original_build_access) and not getattr(subscription_guard, "_ntg_expiry_access_patch_applied", False):
            def patched_build_access(sub: Optional[Dict[str, Any]]) -> Dict[str, Any]:
                access = original_build_access(sub)
                if _is_expired(sub):
                    access["allowed"] = False
                    access["reason"] = "expired"
                    access["status"] = "expired"
                    access["upgrade_required"] = True
                return access

            subscription_guard._build_access = patched_build_access  # type: ignore[attr-defined]
            subscription_guard._ntg_expiry_access_patch_applied = True  # type: ignore[attr-defined]
    except Exception:
        pass

    try:
        from app.services import account_entitlements_service as entitlements

        original_entitlements_from_subscription = getattr(entitlements, "_entitlements_from_subscription", None)
        if callable(original_entitlements_from_subscription) and not getattr(entitlements, "_ntg_expiry_entitlements_patch_applied", False):
            def patched_entitlements_from_subscription(account_id: str, row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
                if _is_expired(row) or not _is_subscription_active(row):
                    return None
                return original_entitlements_from_subscription(account_id, row)

            entitlements._entitlements_from_subscription = patched_entitlements_from_subscription  # type: ignore[attr-defined]
            entitlements._ntg_expiry_entitlements_patch_applied = True  # type: ignore[attr-defined]
    except Exception:
        pass

    billing._ntg_expiry_payload_patch_applied = True  # type: ignore[attr-defined]


def _apply_channel_expiry_patch() -> None:
    """Apply the same expiry rules to WhatsApp/Telegram services and route imports."""

    def patched_get_user_subscription(account_id: str) -> Optional[Dict[str, Any]]:
        return _active_subscription_for_account(account_id)

    def patched_has_active_subscription(account_id: str) -> bool:
        return bool(_active_subscription_for_account(account_id))

    def patched_format_subscription_message(account_id: str) -> str:
        sub = _active_subscription_for_account(account_id)
        expired = _expired_paid_subscription_for_account(account_id)
        if not sub:
            if expired:
                expiry = _date_label(_subscription_expiry_value(expired))
                previous = _clean(expired.get("plan_code") or expired.get("plan_name") or "previous paid plan")
                return (
                    "📋 *NO ACTIVE SUBSCRIPTION*\n\n"
                    f"Previous plan: {previous}\n"
                    f"Expired: {expiry}\n"
                    "Status: expired\n\n"
                    "Renew before using paid AI answers, top-ups, custom deadlines, or paid workspace features.\n\n"
                    "Reply with 4 to see available plans and renew."
                )
            return (
                "📋 *NO ACTIVE SUBSCRIPTION*\n\n"
                "You are currently on free access.\n"
                "Free access supports basic calculators, database/library answers, and non-AI quiz attempts.\n\n"
                "Reply with 4 to see available plans and upgrade."
            )

        plan_code = _clean(sub.get("plan_code") or "unknown")
        plan = None
        try:
            from app.services import channel_subscription_service as css
            plan = css.validate_plan_code(plan_code)  # type: ignore[attr-defined]
        except Exception:
            plan = None

        plan_name = _clean((plan or {}).get("full_name") or sub.get("plan_name") or plan_code.replace("_", " ").title())
        credits = (plan or {}).get("credits") or sub.get("included_credits") or sub.get("ai_credits_total") or "?"
        monthly_credits = (plan or {}).get("monthly_credits") or credits
        billing_cycle = (plan or {}).get("billing_cycle") or ("yearly" if "yearly" in plan_code else "quarterly" if "quarterly" in plan_code else "monthly")
        expiry = _date_label(_subscription_expiry_value(sub))

        if billing_cycle == "monthly":
            credit_display = f"{credits} AI credits per month"
            access_text = f"✨ You have {credits} AI credits to use this month."
        elif billing_cycle == "quarterly":
            credit_display = f"{credits} AI credits per quarter ({monthly_credits} per month)"
            access_text = f"✨ You have {credits} AI credits to use over the next 3 months."
        else:
            credit_display = f"{credits} AI credits per year ({monthly_credits} per month)"
            access_text = f"✨ You have {credits} AI credits to use over the next year."

        return (
            "📋 *YOUR SUBSCRIPTION*\n\n"
            f"✅ Plan: {plan_name}\n"
            f"🎯 Credits: {credit_display}\n"
            "📊 Daily limit: Unlimited ✨\n"
            f"📅 Next billing/expiry: {expiry}\n\n"
            f"{access_text}\n"
            f"🔄 Billing cycle: {billing_cycle}\n\n"
            "To cancel or fix billing issues, contact support."
        )

    def patched_get_credit_balance_with_subscription(account_id: str, base_balance: int):
        if patched_has_active_subscription(account_id):
            sub = _active_subscription_for_account(account_id)
            plan = None
            try:
                from app.services import channel_subscription_service as css
                plan = css.validate_plan_code((sub or {}).get("plan_code", ""))  # type: ignore[attr-defined]
            except Exception:
                plan = None
            if plan:
                return plan.get("credits", 0), "subscription"
        if _expired_paid_subscription_for_account(account_id):
            return 0, "expired"
        return base_balance, "free"

    try:
        from app.services import channel_subscription_service as css

        css.get_user_subscription = patched_get_user_subscription  # type: ignore[attr-defined]
        css.has_active_subscription = patched_has_active_subscription  # type: ignore[attr-defined]
        css.format_subscription_message = patched_format_subscription_message  # type: ignore[attr-defined]
        css.get_credit_balance_with_subscription = patched_get_credit_balance_with_subscription  # type: ignore[attr-defined]
        css._ntg_channel_expiry_patch_applied = True  # type: ignore[attr-defined]
    except Exception:
        pass

    try:
        from app.services import channel_credit_service as ccs

        original_create_credit_payment = getattr(ccs, "create_credit_payment", None)
        original_get_credit_balance = getattr(ccs, "get_credit_balance", None)

        def patched_channel_credit_balance(account_id: str) -> int:
            if _expired_paid_subscription_for_account(account_id):
                return 0
            return int(original_get_credit_balance(account_id)) if callable(original_get_credit_balance) else 0

        if callable(original_get_credit_balance):
            ccs.get_credit_balance = patched_channel_credit_balance  # type: ignore[attr-defined]

        if callable(original_create_credit_payment):
            def patched_create_credit_payment(account_id: str, package_num: int, channel_type: str, provider_user_id: str) -> Dict[str, Any]:
                if not patched_has_active_subscription(account_id):
                    return {
                        "ok": False,
                        "error": "active_paid_subscription_required",
                        "message": EXPIRED_TOPUP_MESSAGE,
                    }
                return original_create_credit_payment(account_id, package_num, channel_type, provider_user_id)

            ccs.create_credit_payment = patched_create_credit_payment  # type: ignore[attr-defined]
        ccs._ntg_topup_active_plan_guard_applied = True  # type: ignore[attr-defined]
    except Exception:
        pass

    try:
        from app.services import credit_usage_service as cus

        def patched_credit_usage_get_subscription(account_id: str) -> Optional[Dict[str, Any]]:
            return _effective_subscription_for_account(account_id)

        def patched_credit_usage_get_effective_plan(account_id: str) -> Dict[str, Any]:
            sub = patched_credit_usage_get_subscription(account_id)
            active = _is_subscription_active(sub)
            raw_code = _lower((sub or {}).get("plan_code") or (sub or {}).get("plan") or (sub or {}).get("tier") or "free")
            if sub and _is_expired(sub):
                plan_code = "expired"
                family = "expired"
            else:
                plan_code = raw_code or "free"
                family = cus.plan_family_from_code(plan_code)  # type: ignore[attr-defined]
            return {
                "subscription": sub,
                "plan_code": plan_code,
                "plan_family": family,
                "active": active,
                "is_paid": bool(active and cus.is_paid_plan_code(plan_code)),  # type: ignore[attr-defined]
            }

        cus.get_subscription = patched_credit_usage_get_subscription  # type: ignore[attr-defined]
        cus.get_effective_plan = patched_credit_usage_get_effective_plan  # type: ignore[attr-defined]
        cus._ntg_credit_usage_expiry_patch_applied = True  # type: ignore[attr-defined]
    except Exception:
        pass

    try:
        from app.routes import telegram as tg

        tg.has_active_subscription = patched_has_active_subscription  # type: ignore[attr-defined]
        tg.format_subscription_message = patched_format_subscription_message  # type: ignore[attr-defined]
        tg.get_credit_balance_with_subscription = patched_get_credit_balance_with_subscription  # type: ignore[attr-defined]
        try:
            from app.services import channel_credit_service as ccs2
            tg.get_credit_balance = ccs2.get_credit_balance  # type: ignore[attr-defined]
            tg.create_credit_payment = ccs2.create_credit_payment  # type: ignore[attr-defined]
        except Exception:
            pass

        def patched_telegram_subscription_row(account_id: str) -> Optional[Dict[str, Any]]:
            return _active_subscription_for_account(account_id)

        tg._subscription_row = patched_telegram_subscription_row  # type: ignore[attr-defined]
        tg._ntg_telegram_subscription_row_patch_applied = True  # type: ignore[attr-defined]
    except Exception:
        pass

    try:
        from app.services import web_quiz_service as wqs
        wqs.has_active_subscription = patched_has_active_subscription  # type: ignore[attr-defined]
    except Exception:
        pass

    try:
        from app.routes import whatsapp as w

        original_init_checkout = getattr(w, "_init_paystack_checkout", None)
        original_credit_balance = getattr(w, "_credit_balance", None)

        def whatsapp_effective_subscription(account_id: str) -> Optional[Dict[str, Any]]:
            return _effective_subscription_for_account(account_id)

        def whatsapp_active_subscription(account_id: str) -> Optional[Dict[str, Any]]:
            return _active_subscription_for_account(account_id)

        def whatsapp_expired_subscription(account_id: str) -> Optional[Dict[str, Any]]:
            return _expired_paid_subscription_for_account(account_id)

        def patched_whatsapp_get_subscription(account_id: str) -> Optional[Dict[str, Any]]:
            return whatsapp_effective_subscription(account_id)

        def patched_whatsapp_is_active_paid_subscription(account_id: str) -> bool:
            return bool(whatsapp_active_subscription(account_id))

        def patched_whatsapp_subscription_status(account_id: str) -> str:
            sub = whatsapp_effective_subscription(account_id)
            if not sub:
                return "inactive"
            if _is_expired(sub):
                return "expired"
            return _lower(sub.get("status") or ("active" if _is_subscription_active(sub) else "inactive"))

        def patched_whatsapp_subscription_expiry(account_id: str) -> str:
            sub = whatsapp_effective_subscription(account_id)
            return _clean(_subscription_expiry_value(sub)) if sub else ""

        def patched_whatsapp_current_plan_code(account_id: str) -> str:
            sub = whatsapp_effective_subscription(account_id)
            if not sub:
                return "free"
            if _is_expired(sub):
                return "expired"
            return _lower(sub.get("plan_code") or sub.get("plan") or "free")

        def patched_whatsapp_same_active_plan(account_id: str, selected_plan_code: str) -> bool:
            sub = whatsapp_active_subscription(account_id)
            return bool(sub and _lower(sub.get("plan_code")) == _lower(selected_plan_code))

        def patched_whatsapp_plan_label(account_id: str) -> str:
            active_sub = whatsapp_active_subscription(account_id)
            expired_sub = whatsapp_expired_subscription(account_id)
            if active_sub:
                name = _clean(active_sub.get("plan_name") or active_sub.get("plan_code") or "Paid plan")
                status = _clean(active_sub.get("status") or "active")
                expires = _subscription_expiry_value(active_sub)
                if expires:
                    return f"{name} ({status})\nExpires: {_date_label(expires)}"
                return f"{name} ({status})"
            if expired_sub:
                previous = _clean(expired_sub.get("plan_code") or expired_sub.get("plan_name") or "previous paid plan")
                expires = _subscription_expiry_value(expired_sub)
                return (
                    f"{previous} (expired)\n"
                    f"Expired: {_date_label(expires)}\n"
                    "Paid access is not active. Reply 4 to renew."
                )
            return "Free Forever"

        def patched_whatsapp_credit_balance(account_id: str) -> int:
            if whatsapp_expired_subscription(account_id):
                return 0
            return int(original_credit_balance(account_id)) if callable(original_credit_balance) else 0

        if callable(original_init_checkout):
            def patched_whatsapp_init_checkout(account: Optional[Dict[str, Any]], account_id: str, item: Dict[str, Any], payment_type: str, wa_id: str = "") -> Dict[str, Any]:
                if _lower(payment_type) == "topup" and not patched_has_active_subscription(account_id):
                    return {
                        "ok": False,
                        "error": "active_paid_subscription_required",
                        "message": EXPIRED_TOPUP_MESSAGE,
                    }
                return original_init_checkout(account, account_id, item, payment_type, wa_id)

            w._init_paystack_checkout = patched_whatsapp_init_checkout  # type: ignore[attr-defined]

        w._get_subscription = patched_whatsapp_get_subscription  # type: ignore[attr-defined]
        w._is_active_paid_subscription = patched_whatsapp_is_active_paid_subscription  # type: ignore[attr-defined]
        w._subscription_status = patched_whatsapp_subscription_status  # type: ignore[attr-defined]
        w._subscription_expiry = patched_whatsapp_subscription_expiry  # type: ignore[attr-defined]
        w._current_plan_code = patched_whatsapp_current_plan_code  # type: ignore[attr-defined]
        w._same_active_plan = patched_whatsapp_same_active_plan  # type: ignore[attr-defined]
        w._plan_label = patched_whatsapp_plan_label  # type: ignore[attr-defined]
        w._credit_balance = patched_whatsapp_credit_balance  # type: ignore[attr-defined]
        w._ntg_whatsapp_expired_plan_helpers_applied = True  # type: ignore[attr-defined]
    except Exception:
        pass


def apply_whatsapp_display_patch() -> None:
    """
    Keep WhatsApp calculator display aligned with web/Telegram and apply shared
    runtime patches that must be available after routes are imported.
    """
    try:
        from app.services.billing_payment_patch import apply_billing_payment_patch
        apply_billing_payment_patch()
    except Exception:
        pass

    _apply_topup_pricing_patch()
    _apply_subscription_expiry_patch()
    _apply_channel_expiry_patch()

    try:
        from app.services.answer_metadata_patch import apply_answer_metadata_patch
        apply_answer_metadata_patch()
    except Exception:
        pass

    try:
        from app.routes import whatsapp as w
    except Exception:
        return

    def _money_precise(amount: Any) -> str:
        try:
            value = float(amount or 0)
        except Exception:
            value = 0.0

        if abs(value - round(value)) < 0.005:
            return f"₦{round(value):,.0f}"
        return f"₦{value:,.2f}"

    def _calculate_paye_precise(text: str) -> str:
        amounts = w._extract_amounts(text)
        norm = w._normalize_text(text)

        if not amounts:
            return (
                "👥 *PAYE Calculator*\n\n"
                "Send salary like this:\n"
                "C1 250000 monthly\n"
                "or\n"
                "C1 3000000 yearly\n\n"
                "For company-specific payroll deductions, use:\n"
                "C1 salary 250000 pension 8% nhf 2.5% hmo 5000 loan 10000 monthly\n\n"
                "Supported deductions: pension, voluntary pension, NHF, HMO, loan, cooperative, union, other.\n"
                "This basic calculator is free. 🧮"
            )

        amount = amounts[0]
        is_monthly = "month" in norm or "monthly" in norm
        annual = amount * 12 if is_monthly else amount
        payroll = w._parse_payroll_deductions(text, annual, is_monthly)
        relief = max(200000, int(annual * 0.01)) + int(annual * 0.20)
        taxable = max(0, annual - relief - int(payroll["taxable_deductions"]))

        bands = [(300000, 0.07), (300000, 0.11), (500000, 0.15), (500000, 0.19), (1600000, 0.21), (10**15, 0.24)]
        remaining = taxable
        tax = 0.0
        for band, rate in bands:
            if remaining <= 0:
                break
            take = min(remaining, band)
            tax += take * rate
            remaining -= take

        monthly_tax = tax / 12
        monthly_gross = annual / 12
        monthly_all_deductions = int(payroll["total_deductions"]) / 12
        net_monthly = monthly_gross - monthly_tax - monthly_all_deductions

        deduction_section = ""
        if payroll["lines"]:
            deduction_section = "\n🏢 Company payroll deductions used:\n" + "\n".join(payroll["lines"]) + "\n"

        return (
            "👥 *PAYE Calculator Result*\n\n"
            f"Gross annual income: {_money_precise(annual)}\n"
            f"Estimated annual relief: {_money_precise(relief)}\n"
            f"Tax-deductible payroll deductions: {_money_precise(float(payroll['taxable_deductions']))}\n"
            f"Estimated taxable income: {_money_precise(taxable)}\n"
            f"Estimated annual PAYE: {_money_precise(round(tax))}\n"
            f"Estimated monthly PAYE: {_money_precise(monthly_tax)}\n"
            f"Estimated monthly net after PAYE/deductions: {_money_precise(net_monthly)}\n"
            f"{deduction_section}\n"
            "⚠️ Note: This is an estimate. Nigerian payroll policies vary by employer. Confirm pension, NHF, allowances, benefits, voluntary deductions, and state-specific treatment before final filing."
        )

    w._money = _money_precise
    w._calculate_paye = _calculate_paye_precise
