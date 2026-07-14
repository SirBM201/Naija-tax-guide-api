from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def _apply_topup_pricing_patch() -> None:
    """
    Keep credit top-up pricing aligned with the approved public pricing ladder.

    This runtime patch is intentionally applied after route modules are imported.
    app.routes.billing reads TOPUP_PACKAGES at request time, so mutating the
    module-level dictionaries here updates package listing, checkout amount,
    callback metadata, and aliases without touching payment secrets or Paystack
    service logic.
    """
    try:
        from app.routes import billing
    except Exception:
        return

    restored_packages: Dict[str, Dict[str, Any]] = {
        "TOPUP_10": {
            "code": "TOPUP_10",
            "name": "10 Usage Credits",
            "description": "Add 10 AI/usage credits to an active paid account.",
            "credits": 10,
            "amount_ngn": 500,
            "amount_kobo": 500 * 100,
            "currency": "NGN",
            "paid_plan_required": True,
        },
        "TOPUP_50": {
            "code": "TOPUP_50",
            "name": "50 Usage Credits",
            "description": "Add 50 AI/usage credits to an active paid account.",
            "credits": 50,
            "amount_ngn": 2000,
            "amount_kobo": 2000 * 100,
            "currency": "NGN",
            "paid_plan_required": True,
        },
        "TOPUP_100": {
            "code": "TOPUP_100",
            "name": "100 Usage Credits",
            "description": "Add 100 AI/usage credits to an active paid account.",
            "credits": 100,
            "amount_ngn": 3500,
            "amount_kobo": 3500 * 100,
            "currency": "NGN",
            "paid_plan_required": True,
        },
        "TOPUP_500": {
            "code": "TOPUP_500",
            "name": "500 Usage Credits",
            "description": "Add 500 AI/usage credits to an active paid account.",
            "credits": 500,
            "amount_ngn": 15000,
            "amount_kobo": 15000 * 100,
            "currency": "NGN",
            "paid_plan_required": True,
        },
    }

    aliases = {
        "10": "TOPUP_10",
        "50": "TOPUP_50",
        "100": "TOPUP_100",
        "500": "TOPUP_500",
        "T10": "TOPUP_10",
        "T50": "TOPUP_50",
        "T100": "TOPUP_100",
        "T500": "TOPUP_500",
        "TOPUP10": "TOPUP_10",
        "TOPUP50": "TOPUP_50",
        "TOPUP100": "TOPUP_100",
        "TOPUP500": "TOPUP_500",
        "TOPUP_10": "TOPUP_10",
        "TOPUP_50": "TOPUP_50",
        "TOPUP_100": "TOPUP_100",
        "TOPUP_500": "TOPUP_500",
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


def _apply_subscription_expiry_patch() -> None:
    """
    Never let a stale DB status='active' override an expired billing period.

    Some older subscription rows can still have status='active' after expires_at
    is already in the past. The billing page and workspace state consume the
    response payload, so the payload itself must be normalized, not only the
    internal active-check helper.
    """
    try:
        from app.routes import billing
    except Exception:
        return

    if getattr(billing, "_ntg_expiry_payload_patch_applied", False):
        return

    original_is_active = getattr(billing, "_subscription_is_active", None)
    original_payload = getattr(billing, "_subscription_payload", None)

    def _expiry_dt(row: Optional[Dict[str, Any]]) -> Optional[datetime]:
        if not row:
            return None
        try:
            raw = billing._subscription_expiry(row)  # type: ignore[attr-defined]
        except Exception:
            raw = (
                row.get("expires_at")
                or row.get("current_period_end")
                or row.get("ends_at")
                or row.get("period_end")
                or row.get("grace_until")
                or row.get("trial_until")
            )
        try:
            parsed = billing._parse_dt(raw)  # type: ignore[attr-defined]
            return parsed
        except Exception:
            return _safe_dt(raw)

    def _now() -> datetime:
        try:
            return billing._now()  # type: ignore[attr-defined]
        except Exception:
            return datetime.now(timezone.utc)

    def _raw_status(row: Optional[Dict[str, Any]]) -> str:
        try:
            return billing._lower((row or {}).get("status"))  # type: ignore[attr-defined]
        except Exception:
            return str((row or {}).get("status") or "").strip().lower()

    def _is_expired(row: Optional[Dict[str, Any]]) -> bool:
        expiry = _expiry_dt(row)
        return bool(expiry and expiry <= _now())

    def _derived_status(row: Optional[Dict[str, Any]], active: bool) -> str:
        raw = _raw_status(row)
        if _is_expired(row):
            return "expired"
        if active:
            return raw or "active"
        if raw in {"inactive", "expired", "cancelled", "canceled", "disabled", "paused", "failed"}:
            return raw
        return "inactive"

    if callable(original_is_active):
        def patched_subscription_is_active(row: Optional[Dict[str, Any]]) -> bool:
            if _is_expired(row):
                return False
            return bool(original_is_active(row))

        billing._subscription_is_active = patched_subscription_is_active  # type: ignore[attr-defined]

    if callable(original_payload):
        def patched_subscription_payload(
            account_id: str,
            sub: Optional[Dict[str, Any]],
            account: Optional[Dict[str, Any]] = None,
        ) -> Dict[str, Any]:
            payload = original_payload(account_id, sub, account)
            if not sub:
                return payload

            active = bool(billing._subscription_is_active(sub))  # type: ignore[attr-defined]
            status = _derived_status(sub, active)

            payload["active"] = active
            payload["is_active"] = active
            payload["status"] = status
            payload["expired"] = status == "expired"

            if status == "expired":
                payload["topup_allowed"] = False
                payload["topup_eligibility_reason"] = "subscription_expired"

            subscription = payload.get("subscription")
            if isinstance(subscription, dict):
                subscription["active"] = active
                subscription["is_active"] = active
                subscription["status"] = status
                subscription["expired"] = status == "expired"

            summary = payload.get("subscription_summary")
            if isinstance(summary, dict):
                summary["is_active_now"] = active
                summary["status"] = status

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

    billing._ntg_expiry_payload_patch_applied = True  # type: ignore[attr-defined]


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

        bands = [
            (300000, 0.07),
            (300000, 0.11),
            (500000, 0.15),
            (500000, 0.19),
            (1600000, 0.21),
            (10**15, 0.24),
        ]
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
            deduction_section = (
                "\n🏢 Company payroll deductions used:\n"
                + "\n".join(payroll["lines"])
                + "\n"
            )

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
