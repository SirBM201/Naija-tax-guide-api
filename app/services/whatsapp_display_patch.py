from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional


PAID_FAMILIES = {"starter", "professional", "business"}
EXPIRED_TOPUP_MESSAGE = (
    "⚠️ *Active paid plan required*\n\n"
    "Usage Credit add-ons are available only while a paid subscription is active. "
    "Your paid plan appears to be expired, so top-up checkout cannot start.\n\n"
    "Reply 4 to renew or choose a subscription plan."
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
            1 if _plan_family_from_code(row.get("plan_code") or row.get("plan") or row.get("tier")) in PAID_FAMILIES else 0,
            _row_sort_dt(row),
        ),
        reverse=True,
    )
    return ranked[0]


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

    def billing_expiry_dt(row: Optional[Dict[str, Any]]) -> Optional[datetime]:
        if not row:
            return None
        try:
            raw = billing._subscription_expiry(row)  # type: ignore[attr-defined]
        except Exception:
            raw = _subscription_expiry_value(row)
        try:
            return billing._parse_dt(raw)  # type: ignore[attr-defined]
        except Exception:
            return _safe_dt(raw)

    def billing_is_expired(row: Optional[Dict[str, Any]]) -> bool:
        expiry = billing_expiry_dt(row)
        return bool(expiry and expiry <= _now_utc())

    def derived_status(row: Optional[Dict[str, Any]], active: bool) -> str:
        raw = _lower((row or {}).get("status"))
        if billing_is_expired(row):
            return "expired"
        if active:
            return raw or "active"
        if raw in {"inactive", "expired", "cancelled", "canceled", "disabled", "paused", "failed"}:
            return raw
        return "inactive"

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

            active = bool(billing._subscription_is_active(sub))  # type: ignore[attr-defined]
            status = derived_status(sub, active)
            expired = status == "expired"
            original_plan_code = _clean(payload.get("plan_code") or (sub or {}).get("plan_code"))
            original_plan_name = _clean(payload.get("plan_name") or (sub or {}).get("plan_name") or (sub or {}).get("name"))

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
                subscription["active"] = active
                subscription["is_active"] = active
                subscription["status"] = status
                subscription["expired"] = expired
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

    try:
        from app.services import channel_subscription_service as css
    except Exception:
        css = None  # type: ignore

    def get_rows_for_account(account_id: str) -> list[Dict[str, Any]]:
        rows: list[Dict[str, Any]] = []
        if css is None:
            return rows
        try:
            res = (
                css._sb().table("user_subscriptions")  # type: ignore[attr-defined]
                .select("*")
                .eq("account_id", account_id)
                .order("current_period_end", desc=True)
                .order("updated_at", desc=True)
                .limit(50)
                .execute()
            )
            rows.extend([r for r in (getattr(res, "data", None) or []) if isinstance(r, dict)])
        except Exception:
            try:
                res = css._sb().table("user_subscriptions").select("*").eq("account_id", account_id).limit(50).execute()  # type: ignore[attr-defined]
                rows.extend([r for r in (getattr(res, "data", None) or []) if isinstance(r, dict)])
            except Exception:
                pass
        return rows

    def patched_get_user_subscription(account_id: str) -> Optional[Dict[str, Any]]:
        account_id = _clean(account_id)
        if not account_id:
            return None
        selected = _select_effective_subscription(get_rows_for_account(account_id))
        return selected if _is_active_paid_subscription(selected) else None

    def patched_has_active_subscription(account_id: str) -> bool:
        return bool(patched_get_user_subscription(account_id))

    def patched_format_subscription_message(account_id: str) -> str:
        sub = patched_get_user_subscription(account_id)
        if not sub:
            return (
                "📋 *NO ACTIVE SUBSCRIPTION*\n\n"
                "Your paid plan is not currently active. If it has expired, please renew before using paid AI answers or buying top-ups.\n\n"
                "Free access still supports basic calculators, database/library answers, and non-AI quiz attempts.\n\n"
                "Reply with 4 to see available plans and renew."
            )

        plan_code = _clean(sub.get("plan_code") or "unknown")
        plan = None
        if css is not None:
            try:
                plan = css.validate_plan_code(plan_code)  # type: ignore[attr-defined]
            except Exception:
                plan = None

        plan_name = _clean((plan or {}).get("full_name") or sub.get("plan_name") or plan_code.replace("_", " ").title())
        credits = (plan or {}).get("credits") or sub.get("included_credits") or sub.get("ai_credits_total") or "?"
        monthly_credits = (plan or {}).get("monthly_credits") or credits
        billing_cycle = (plan or {}).get("billing_cycle") or ("yearly" if "yearly" in plan_code else "quarterly" if "quarterly" in plan_code else "monthly")
        expiry_raw = _subscription_expiry_value(sub)
        expiry_text = ""
        if expiry_raw:
            dt = _safe_dt(expiry_raw)
            expiry_text = f"\n📅 Next billing/expiry: {dt.strftime('%b %d, %Y') if dt else str(expiry_raw)[:10]}"

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
            "📊 Daily limit: Unlimited ✨"
            f"{expiry_text}\n\n"
            f"{access_text}\n"
            f"🔄 Billing cycle: {billing_cycle}\n\n"
            "To cancel or fix billing issues, contact support."
        )

    def patched_get_credit_balance_with_subscription(account_id: str, base_balance: int):
        if patched_has_active_subscription(account_id):
            sub = patched_get_user_subscription(account_id)
            plan = None
            if css is not None and sub:
                try:
                    plan = css.validate_plan_code(sub.get("plan_code", ""))  # type: ignore[attr-defined]
                except Exception:
                    plan = None
            if plan:
                return plan.get("credits", 0), "subscription"
        return base_balance, "free_or_expired"

    if css is not None and not getattr(css, "_ntg_channel_expiry_patch_applied", False):
        css.get_user_subscription = patched_get_user_subscription  # type: ignore[attr-defined]
        css.has_active_subscription = patched_has_active_subscription  # type: ignore[attr-defined]
        css.format_subscription_message = patched_format_subscription_message  # type: ignore[attr-defined]
        css.get_credit_balance_with_subscription = patched_get_credit_balance_with_subscription  # type: ignore[attr-defined]
        css._ntg_channel_expiry_patch_applied = True  # type: ignore[attr-defined]

    try:
        from app.services import channel_credit_service as ccs
        original_create_credit_payment = getattr(ccs, "create_credit_payment", None)
        if callable(original_create_credit_payment) and not getattr(ccs, "_ntg_topup_active_plan_guard_applied", False):
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
        original_get_subscription = getattr(cus, "get_subscription", None)
        if callable(original_get_subscription) and not getattr(cus, "_ntg_credit_usage_expiry_patch_applied", False):
            def patched_credit_usage_get_subscription(account_id: str) -> Optional[Dict[str, Any]]:
                selected = _select_effective_subscription(get_rows_for_account(account_id))
                if selected:
                    return selected
                return original_get_subscription(account_id)

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
            tg.create_credit_payment = ccs2.create_credit_payment  # type: ignore[attr-defined]
        except Exception:
            pass

        original_subscription_row = getattr(tg, "_subscription_row", None)
        if callable(original_subscription_row) and not getattr(tg, "_ntg_telegram_subscription_row_patch_applied", False):
            def patched_telegram_subscription_row(account_id: str) -> Optional[Dict[str, Any]]:
                row = patched_get_user_subscription(account_id)
                return row if _is_active_paid_subscription(row) else None

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
        if callable(original_init_checkout) and not getattr(w, "_ntg_whatsapp_topup_guard_applied", False):
            def patched_whatsapp_init_checkout(account: Optional[Dict[str, Any]], account_id: str, item: Dict[str, Any], payment_type: str, wa_id: str = "") -> Dict[str, Any]:
                if _lower(payment_type) == "topup" and not patched_has_active_subscription(account_id):
                    return {
                        "ok": False,
                        "error": "active_paid_subscription_required",
                        "message": EXPIRED_TOPUP_MESSAGE,
                    }
                return original_init_checkout(account, account_id, item, payment_type, wa_id)

            w._init_paystack_checkout = patched_whatsapp_init_checkout  # type: ignore[attr-defined]
            w._ntg_whatsapp_topup_guard_applied = True  # type: ignore[attr-defined]
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
