# app/services/tax_rules/vat_rules.py
from __future__ import annotations

from typing import Dict, Any


def explain_vat_basic() -> Dict[str, Any]:
    return {
        "ok": True,
        "answer": (
            "VAT in Nigeria stands for Value Added Tax. "
            "It is a consumption tax charged on taxable goods and services."
        ),
        "basis": [
            "VAT applies to value added on taxable supplies.",
            "The current standard VAT rate in Nigeria is 7.5%."
        ],
        "confidence": 0.98,
        "source_type": "rules_engine",
    }
