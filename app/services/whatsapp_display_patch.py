from __future__ import annotations

import re
from typing import Any, Dict


def apply_whatsapp_display_patch() -> None:
    """
    Keep WhatsApp calculator display aligned with web/Telegram.

    The original WhatsApp PAYE formatter rounded all monetary values to whole naira.
    That made monthly PAYE look different from the web/Telegram result even though
    the underlying calculation was correct. This patch is applied during app boot
    after app.routes.whatsapp has been imported.
    """
    try:
        from app.services.billing_payment_patch import apply_billing_payment_patch

        apply_billing_payment_patch()
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
