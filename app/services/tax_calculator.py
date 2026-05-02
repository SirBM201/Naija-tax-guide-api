# app/services/tax_calculator.py
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone

from app.core.supabase_client import supabase

logger = logging.getLogger(__name__)


def _sb():
    return supabase() if callable(supabase) else supabase


def get_paye_brackets() -> List[Dict[str, Any]]:
    """Get PAYE tax brackets from database"""
    try:
        result = _sb().table("paye_brackets") \
            .select("band_min, band_max, rate, sort_order") \
            .order("sort_order") \
            .execute()
        return result.data or []
    except Exception as e:
        logger.error(f"Error fetching PAYE brackets: {e}")
        # Fallback brackets
        return [
            {"band_min": 0, "band_max": 300000, "rate": 7.0},
            {"band_min": 300001, "band_max": 600000, "rate": 11.0},
            {"band_min": 600001, "band_max": 1100000, "rate": 15.0},
            {"band_min": 1100001, "band_max": 1600000, "rate": 19.0},
            {"band_min": 1600001, "band_max": 3200000, "rate": 21.0},
            {"band_min": 3200001, "band_max": None, "rate": 24.0},
        ]


def calculate_paye(gross_income: float, pension_contribution: float = 0, nhf: float = 0) -> Dict[str, Any]:
    """
    Calculate PAYE tax for an employee
    
    Args:
        gross_income: Monthly gross income in Naira
        pension_contribution: Monthly pension contribution (max 8% of gross)
        nhf: National Housing Fund contribution
    
    Returns:
        Dict with tax breakdown and total payable
    """
    gross_annual = gross_income * 12
    
    # Consolidated Relief Allowance (CRA)
    # Higher of ₦200,000 OR 1% of gross income, plus 20% of gross income
    cra_fixed = max(200000, gross_annual * 0.01)
    cra_percentage = gross_annual * 0.20
    cra_total = cra_fixed + cra_percentage
    
    # Pension deduction (max 8% of annual gross)
    pension_annual = min(pension_contribution * 12, gross_annual * 0.08)
    
    # NHF deduction
    nhf_annual = nhf * 12
    
    # Total deductions
    total_deductions = cra_total + pension_annual + nhf_annual
    
    # Chargeable Income
    chargeable_income = max(0, gross_annual - total_deductions)
    
    # Calculate tax using bands
    brackets = get_paye_brackets()
    tax_payable = 0
    tax_breakdown = []
    remaining = chargeable_income
    
    for bracket in brackets:
        band_min = bracket.get("band_min", 0)
        band_max = bracket.get("band_max")
        rate = bracket.get("rate", 0) / 100
        
        if remaining <= 0:
            break
        
        if band_max is not None:
            band_amount = min(remaining, band_max - band_min + 1)
        else:
            band_amount = remaining
        
        if band_amount > 0:
            band_tax = band_amount * rate
            tax_payable += band_tax
            tax_breakdown.append({
                "band_min": band_min,
                "band_max": band_max,
                "rate": rate * 100,
                "taxable_amount": band_amount,
                "tax": band_tax
            })
            remaining -= band_amount
    
    monthly_tax = tax_payable / 12
    
    return {
        "annual_gross": gross_annual,
        "monthly_gross": gross_income,
        "cra_deduction": cra_total,
        "pension_deduction": pension_annual,
        "nhf_deduction": nhf_annual,
        "total_deductions": total_deductions,
        "chargeable_income": chargeable_income,
        "annual_tax_payable": tax_payable,
        "monthly_tax_payable": monthly_tax,
        "effective_rate": (tax_payable / gross_annual * 100) if gross_annual > 0 else 0,
        "tax_breakdown": tax_breakdown
    }


def calculate_vat(taxable_supplies: float, input_vat: float = 0, vat_rate: float = 7.5) -> Dict[str, Any]:
    """
    Calculate VAT for a business
    
    Args:
        taxable_supplies: Value of taxable supplies (sales)
        input_vat: VAT paid on purchases (deductible)
        vat_rate: VAT rate (default 7.5%)
    
    Returns:
        Dict with VAT calculation
    """
    output_vat = taxable_supplies * (vat_rate / 100)
    vat_payable = max(0, output_vat - input_vat)
    
    return {
        "taxable_supplies": taxable_supplies,
        "vat_rate": vat_rate,
        "output_vat": output_vat,
        "input_vat": input_vat,
        "vat_payable": vat_payable
    }


def calculate_cit(gross_profit: float, allowable_expenses: float, cit_rate: float = 20) -> Dict[str, Any]:
    """
    Calculate Company Income Tax (CIT)
    
    Args:
        gross_profit: Company's gross profit
        allowable_expenses: Deductible expenses
        cit_rate: CIT rate (default 20% for medium companies)
    
    Returns:
        Dict with CIT calculation
    """
    assessable_profit = max(0, gross_profit - allowable_expenses)
    cit_payable = assessable_profit * (cit_rate / 100)
    
    # Determine applicable rate based on company size
    if gross_profit > 100000000:  # Large company > ₦100M
        applicable_rate = 30
        cit_payable = assessable_profit * 0.30
    elif gross_profit > 25000000:  # Medium company ₦25M - ₦100M
        applicable_rate = 20
        cit_payable = assessable_profit * 0.20
    else:  # Small company ≤ ₦25M
        applicable_rate = 0
        cit_payable = 0
    
    return {
        "gross_profit": gross_profit,
        "allowable_expenses": allowable_expenses,
        "assessable_profit": assessable_profit,
        "applicable_rate": applicable_rate,
        "cit_payable": cit_payable,
        "company_size": "large" if gross_profit > 100000000 else "medium" if gross_profit > 25000000 else "small"
    }


def calculate_tax(tax_type: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
    """
    Main tax calculation dispatcher
    
    Args:
        tax_type: 'paye', 'vat', or 'cit'
        inputs: Dictionary of input values
    
    Returns:
        Calculation result
    """
    if tax_type == "paye":
        return calculate_paye(
            gross_income=inputs.get("monthly_gross_income", 0),
            pension_contribution=inputs.get("pension_contribution", 0),
            nhf=inputs.get("nhf", 0)
        )
    elif tax_type == "vat":
        return calculate_vat(
            taxable_supplies=inputs.get("taxable_supplies", 0),
            input_vat=inputs.get("input_vat", 0)
        )
    elif tax_type == "cit":
        return calculate_cit(
            gross_profit=inputs.get("gross_profit", 0),
            allowable_expenses=inputs.get("allowable_expenses", 0)
        )
    else:
        raise ValueError(f"Unknown tax type: {tax_type}")
