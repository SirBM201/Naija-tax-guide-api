from __future__ import annotations

import re
from typing import Any, Dict


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
