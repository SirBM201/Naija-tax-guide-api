# app/services/tax_calculator.py
from __future__ import annotations

from typing import Dict, Any, List
from app.services.calculator_rule_metadata import attach_calculator_metadata


def get_paye_brackets() -> List[Dict[str, Any]]:
    return [
        {"band_min": 0, "band_max": 800000, "rate": 0.0},
        {"band_min": 800000, "band_max": 3000000, "rate": 15.0},
        {"band_min": 3000000, "band_max": 12000000, "rate": 18.0},
        {"band_min": 12000000, "band_max": 25000000, "rate": 21.0},
        {"band_min": 25000000, "band_max": 50000000, "rate": 23.0},
        {"band_min": 50000000, "band_max": None, "rate": 25.0},
    ]


def calculate_paye(gross_income: float, pension_contribution: float = 0, nhf: float = 0) -> Dict[str, Any]:
    gross_income = max(0.0, float(gross_income or 0))
    pension_contribution = max(0.0, float(pension_contribution or 0))
    nhf = max(0.0, float(nhf or 0))
    gross_annual = gross_income * 12
    pension_annual = pension_contribution * 12
    nhf_annual = nhf * 12
    payroll_deductions = pension_annual + nhf_annual
    # The old Consolidated Relief Allowance is not part of the 2026 NTA regime.
    # This V1 calculator applies user-supplied pension/NHF deductions only; other
    # statutory deductions/reliefs should be reflected by future dedicated inputs.
    chargeable_income = max(0.0, gross_annual - payroll_deductions)
    bands = [(800000.0, 0.00), (2200000.0, 0.15), (9000000.0, 0.18), (13000000.0, 0.21), (25000000.0, 0.23), (float("inf"), 0.25)]
    remaining = chargeable_income
    tax_payable = 0.0
    tax_breakdown = []
    for band_amount, rate in bands:
        if remaining <= 0:
            break
        taxable_in_band = min(remaining, band_amount)
        band_tax = taxable_in_band * rate
        tax_payable += band_tax
        tax_breakdown.append({"band_amount": band_amount, "rate": rate * 100, "taxable_amount": taxable_in_band, "tax": band_tax})
        remaining -= taxable_in_band
    monthly_tax = tax_payable / 12
    net_monthly_pay = max(0.0, gross_income - pension_contribution - nhf - monthly_tax)
    return attach_calculator_metadata("paye", {
        "ok": True, "annual_gross": gross_annual, "monthly_gross": gross_income,
        "cra_deduction": 0.0, "pension_deduction": pension_annual, "nhf_deduction": nhf_annual,
        "payroll_deductions": payroll_deductions, "total_deductions": payroll_deductions,
        "chargeable_income": chargeable_income, "annual_tax_payable": tax_payable,
        "monthly_tax_payable": monthly_tax, "effective_rate": (tax_payable / gross_annual * 100) if gross_annual > 0 else 0,
        "tax_breakdown": tax_breakdown, "net_monthly_pay": net_monthly_pay, "annual_gross_income": gross_annual,
        "consolidated_relief": 0.0, "annual_pension": pension_annual, "annual_nhf": nhf_annual,
        "tax_deductible_payroll_deductions": payroll_deductions,
        "explanation": f"Annual Gross: ₦{gross_annual:,.2f}\nPension: ₦{pension_annual:,.2f}\nNHF: ₦{nhf_annual:,.2f}\nChargeable Income: ₦{chargeable_income:,.2f}\nAnnual Tax: ₦{tax_payable:,.2f}\nMonthly Tax: ₦{monthly_tax:,.2f}",
    })


def calculate_vat(taxable_supplies: float, input_vat: float = 0, vat_rate: float = 7.5) -> Dict[str, Any]:
    taxable_supplies = max(0.0, float(taxable_supplies or 0)); input_vat = max(0.0, float(input_vat or 0)); vat_rate = max(0.0, float(vat_rate or 0))
    output_vat = taxable_supplies * (vat_rate / 100); vat_payable = max(0, output_vat - input_vat)
    return attach_calculator_metadata("vat", {"ok": True, "taxable_supplies": taxable_supplies, "vat_rate": vat_rate, "output_vat": output_vat, "input_vat": input_vat, "vat_payable": vat_payable, "explanation": f"VAT on Sales ({vat_rate:g}% of ₦{taxable_supplies:,.2f}) = ₦{output_vat:,.2f}\nInput VAT = ₦{input_vat:,.2f}\nVAT Payable = ₦{vat_payable:,.2f}"})


def calculate_vat_simplified(sales_amount: float, purchases_amount: float = 0, vat_rate: float = 7.5) -> Dict[str, Any]:
    sales_amount = max(0.0, float(sales_amount or 0)); purchases_amount = max(0.0, float(purchases_amount or 0)); vat_rate = max(0.0, float(vat_rate or 0))
    output_vat = sales_amount * (vat_rate / 100); input_vat = purchases_amount * (vat_rate / 100); vat_payable = max(0, output_vat - input_vat)
    return attach_calculator_metadata("vat", {"ok": True, "sales_amount": sales_amount, "purchases_amount": purchases_amount, "vat_rate": vat_rate, "output_vat": output_vat, "input_vat": input_vat, "vat_payable": vat_payable, "explanation": f"VAT on sales ({vat_rate:g}%): ₦{output_vat:,.2f}\nInput VAT estimate: ₦{input_vat:,.2f}\nVAT to pay: ₦{vat_payable:,.2f}"})


def _cit_result(revenue: float, expenses: float, *, traditional: bool = False) -> Dict[str, Any]:
    revenue = max(0.0, float(revenue or 0)); expenses = max(0.0, float(expenses or 0)); profit = max(0, revenue - expenses)
    if revenue <= 50000000:
        applicable_rate, company_size, company_size_label = 0, "Small", "Potential small company (turnover ≤ ₦50M)"
        classification_warning = "0% assumes the company also satisfies the NTA fixed-asset threshold and is not excluded as a professional-services business."
    else:
        applicable_rate, company_size, company_size_label = 30, "Large", "Large company (turnover > ₦50M)"
        classification_warning = None
    cit_payable = profit * (applicable_rate / 100)
    base = {"ok": True, "applicable_rate": applicable_rate, "cit_payable": cit_payable, "company_size": company_size, "company_size_label": company_size_label, "classification_warning": classification_warning}
    if traditional:
        base.update({"gross_profit": revenue, "allowable_expenses": expenses, "assessable_profit": profit, "explanation": f"Profit = ₦{revenue:,.2f} - ₦{expenses:,.2f} = ₦{profit:,.2f}\nClassification: {company_size_label}\nEstimated CIT rate: {applicable_rate}%\nCIT Payable: ₦{cit_payable:,.2f}"})
    else:
        base.update({"revenue": revenue, "expenses": expenses, "profit": profit, "tax_message": f"Estimated CIT rate under the 2026 rule pack: {applicable_rate}%", "explanation": f"Revenue: ₦{revenue:,.2f}\nExpenses: ₦{expenses:,.2f}\nProfit: ₦{profit:,.2f}\nClassification: {company_size_label}\nEstimated CIT rate: {applicable_rate}%\nCIT Payable: ₦{cit_payable:,.2f}"})
    return attach_calculator_metadata("cit", base)


def calculate_cit(gross_profit: float, allowable_expenses: float, cit_rate: float = 30) -> Dict[str, Any]:
    return _cit_result(gross_profit, allowable_expenses, traditional=True)


def calculate_cit_simplified(revenue: float, expenses: float) -> Dict[str, Any]:
    return _cit_result(revenue, expenses, traditional=False)


def calculate_tax(tax_type: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
    tax_type = str(tax_type or "").strip().lower()
    if tax_type == "paye": return calculate_paye(gross_income=inputs.get("monthly_gross_income", 0), pension_contribution=inputs.get("pension_contribution", 0), nhf=inputs.get("nhf", 0))
    if tax_type == "vat":
        if "sales_amount" in inputs and "purchases_amount" in inputs: return calculate_vat_simplified(inputs.get("sales_amount", 0), inputs.get("purchases_amount", 0))
        return calculate_vat(inputs.get("taxable_supplies", 0), inputs.get("input_vat", 0))
    if tax_type == "cit":
        if "revenue" in inputs and "expenses" in inputs: return calculate_cit_simplified(inputs.get("revenue", 0), inputs.get("expenses", 0))
        return calculate_cit(inputs.get("gross_profit", 0), inputs.get("allowable_expenses", 0))
    raise ValueError(f"Unknown tax type: {tax_type}")
