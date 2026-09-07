# app/services/tax_calculator.py
from __future__ import annotations

import logging
from typing import Dict, Any, List

from app.core.supabase_client import supabase
from app.services.calculator_rule_metadata import attach_calculator_metadata

logger = logging.getLogger(__name__)


def _sb():
    return supabase() if callable(supabase) else supabase


def get_paye_brackets() -> List[Dict[str, Any]]:
    try:
        result = _sb().table("paye_brackets").select("band_min, band_max, rate, sort_order").order("sort_order").execute()
        return result.data or []
    except Exception as exc:
        logger.error("Error fetching PAYE brackets: %s", exc)
        return [
            {"band_min": 0, "band_max": 300000, "rate": 7.0},
            {"band_min": 300001, "band_max": 600000, "rate": 11.0},
            {"band_min": 600001, "band_max": 1100000, "rate": 15.0},
            {"band_min": 1100001, "band_max": 1600000, "rate": 19.0},
            {"band_min": 1600001, "band_max": 3200000, "rate": 21.0},
            {"band_min": 3200001, "band_max": None, "rate": 24.0},
        ]


def calculate_paye(gross_income: float, pension_contribution: float = 0, nhf: float = 0) -> Dict[str, Any]:
    gross_income = max(0.0, float(gross_income or 0))
    pension_contribution = max(0.0, float(pension_contribution or 0))
    nhf = max(0.0, float(nhf or 0))
    gross_annual = gross_income * 12
    pension_annual = pension_contribution * 12
    nhf_annual = nhf * 12
    cra_total = max(200000.0, gross_annual * 0.01) + (gross_annual * 0.20)
    payroll_deductions = pension_annual + nhf_annual
    total_deductions = cra_total + payroll_deductions
    chargeable_income = max(0.0, gross_annual - total_deductions)
    bands = [(300000.0, 0.07), (300000.0, 0.11), (500000.0, 0.15), (500000.0, 0.19), (1600000.0, 0.21), (float("inf"), 0.24)]
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
        "ok": True, "annual_gross": gross_annual, "monthly_gross": gross_income, "cra_deduction": cra_total,
        "pension_deduction": pension_annual, "nhf_deduction": nhf_annual, "payroll_deductions": payroll_deductions,
        "total_deductions": total_deductions, "chargeable_income": chargeable_income, "annual_tax_payable": tax_payable,
        "monthly_tax_payable": monthly_tax, "effective_rate": (tax_payable / gross_annual * 100) if gross_annual > 0 else 0,
        "tax_breakdown": tax_breakdown, "net_monthly_pay": net_monthly_pay, "annual_gross_income": gross_annual,
        "consolidated_relief": cra_total, "annual_pension": pension_annual, "annual_nhf": nhf_annual,
        "tax_deductible_payroll_deductions": payroll_deductions,
        "explanation": f"Annual Gross: ₦{gross_annual:,.2f}\nCRA Deduction: ₦{cra_total:,.2f}\nPension: ₦{pension_annual:,.2f}\nNHF: ₦{nhf_annual:,.2f}\nChargeable Income: ₦{chargeable_income:,.2f}\nAnnual Tax: ₦{tax_payable:,.2f}\nMonthly Tax: ₦{monthly_tax:,.2f}",
    })


def calculate_vat(taxable_supplies: float, input_vat: float = 0, vat_rate: float = 7.5) -> Dict[str, Any]:
    taxable_supplies = max(0.0, float(taxable_supplies or 0))
    input_vat = max(0.0, float(input_vat or 0))
    vat_rate = max(0.0, float(vat_rate or 0))
    output_vat = taxable_supplies * (vat_rate / 100)
    vat_payable = max(0, output_vat - input_vat)
    return attach_calculator_metadata("vat", {"ok": True, "taxable_supplies": taxable_supplies, "vat_rate": vat_rate, "output_vat": output_vat, "input_vat": input_vat, "vat_payable": vat_payable, "explanation": f"VAT on Sales ({vat_rate:g}% of ₦{taxable_supplies:,.2f}) = ₦{output_vat:,.2f}\nVAT on Purchases = ₦{input_vat:,.2f}\nVAT Payable = ₦{vat_payable:,.2f}"})


def calculate_vat_simplified(sales_amount: float, purchases_amount: float = 0, vat_rate: float = 7.5) -> Dict[str, Any]:
    sales_amount = max(0.0, float(sales_amount or 0))
    purchases_amount = max(0.0, float(purchases_amount or 0))
    vat_rate = max(0.0, float(vat_rate or 0))
    output_vat = sales_amount * (vat_rate / 100)
    input_vat = purchases_amount * (vat_rate / 100)
    vat_payable = max(0, output_vat - input_vat)
    return attach_calculator_metadata("vat", {"ok": True, "sales_amount": sales_amount, "purchases_amount": purchases_amount, "vat_rate": vat_rate, "output_vat": output_vat, "input_vat": input_vat, "vat_payable": vat_payable, "explanation": f"📈 VAT on sales ({vat_rate:g}% of ₦{sales_amount:,.2f}) = ₦{output_vat:,.2f}\n📉 VAT on purchases ({vat_rate:g}% of ₦{purchases_amount:,.2f}) = ₦{input_vat:,.2f}\n💰 VAT to pay = ₦{vat_payable:,.2f}"})


def _cit_result(revenue: float, expenses: float, *, traditional: bool = False) -> Dict[str, Any]:
    revenue = max(0.0, float(revenue or 0))
    expenses = max(0.0, float(expenses or 0))
    profit = max(0, revenue - expenses)
    if revenue > 100000000:
        applicable_rate, company_size = 30, "Large"
        company_size_label = "Large Company (>₦100M revenue)"
    elif revenue > 25000000:
        applicable_rate, company_size = 20, "Medium"
        company_size_label = "Medium Company (₦25M - ₦100M revenue)"
    else:
        applicable_rate, company_size = 0, "Small"
        company_size_label = "Small Company (≤₦25M revenue)"
    cit_payable = profit * (applicable_rate / 100)
    base = {"ok": True, "applicable_rate": applicable_rate, "cit_payable": cit_payable, "company_size": company_size, "company_size_label": company_size_label}
    if traditional:
        base.update({"gross_profit": revenue, "allowable_expenses": expenses, "assessable_profit": profit, "explanation": f"Profit = ₦{revenue:,.2f} - ₦{expenses:,.2f} = ₦{profit:,.2f}\nCompany Size: {company_size_label}\nTax Rate: {applicable_rate}%\nCIT Payable: ₦{cit_payable:,.2f}"})
    else:
        tax_message = f"Your company is classified as {company_size.upper()}. Estimated CIT rate in this rule pack: {applicable_rate}%"
        base.update({"revenue": revenue, "expenses": expenses, "profit": profit, "tax_message": tax_message, "explanation": f"📊 Revenue: ₦{revenue:,.2f}\n📉 Expenses: ₦{expenses:,.2f}\n📈 Profit: ₦{profit:,.2f}\n🏢 {tax_message}\n💰 CIT Payable: ₦{cit_payable:,.2f}"})
    return attach_calculator_metadata("cit", base)


def calculate_cit(gross_profit: float, allowable_expenses: float, cit_rate: float = 20) -> Dict[str, Any]:
    return _cit_result(gross_profit, allowable_expenses, traditional=True)


def calculate_cit_simplified(revenue: float, expenses: float) -> Dict[str, Any]:
    return _cit_result(revenue, expenses, traditional=False)


def calculate_tax(tax_type: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
    tax_type = str(tax_type or "").strip().lower()
    if tax_type == "paye":
        return calculate_paye(gross_income=inputs.get("monthly_gross_income", 0), pension_contribution=inputs.get("pension_contribution", 0), nhf=inputs.get("nhf", 0))
    if tax_type == "vat":
        if "sales_amount" in inputs and "purchases_amount" in inputs:
            return calculate_vat_simplified(sales_amount=inputs.get("sales_amount", 0), purchases_amount=inputs.get("purchases_amount", 0))
        return calculate_vat(taxable_supplies=inputs.get("taxable_supplies", 0), input_vat=inputs.get("input_vat", 0))
    if tax_type == "cit":
        if "revenue" in inputs and "expenses" in inputs:
            return calculate_cit_simplified(revenue=inputs.get("revenue", 0), expenses=inputs.get("expenses", 0))
        return calculate_cit(gross_profit=inputs.get("gross_profit", 0), allowable_expenses=inputs.get("allowable_expenses", 0))
    raise ValueError(f"Unknown tax type: {tax_type}")
