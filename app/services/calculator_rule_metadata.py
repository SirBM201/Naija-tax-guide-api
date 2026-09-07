from __future__ import annotations

"""Version and provenance metadata for deterministic V1 tax calculators."""

from datetime import date, datetime, timezone
from typing import Any, Dict

CALCULATOR_RULE_PACK_VERSION = "ng-nta-2025-effective-2026-v1"
CALCULATOR_RULES_LAST_REVIEWED = "2026-09-07"
CALCULATOR_RULES_REVIEW_BY = "2026-12-31"

_RULES: Dict[str, Dict[str, Any]] = {
    "paye": {
        "jurisdiction": "Nigeria",
        "rule_family": "Personal Income Tax / PAYE",
        "source_reference": "Nigeria Tax Act 2025, Fourth Schedule / section 58",
        "effective_period": "effective from 1 January 2026",
        "values": {
            "cra": "CRA removed under the 2026 regime; applicable statutory deductions/reliefs are handled separately",
            "bands": ["first NGN 800,000 @ 0%", "next NGN 2,200,000 @ 15%", "next NGN 9,000,000 @ 18%", "next NGN 13,000,000 @ 21%", "next NGN 25,000,000 @ 23%", "remainder above NGN 50,000,000 @ 25%"],
        },
    },
    "vat": {
        "jurisdiction": "Nigeria",
        "rule_family": "Value Added Tax",
        "source_reference": "Nigeria Tax Act 2025, section 148",
        "effective_period": "effective from 1 January 2026",
        "values": {"standard_rate_percent": 7.5},
    },
    "cit": {
        "jurisdiction": "Nigeria",
        "rule_family": "Companies Income Tax",
        "source_reference": "Nigeria Tax Act 2025 company income tax provisions and small-company definition",
        "effective_period": "effective from 1 January 2026",
        "values": {"small_company_max_revenue_ngn": 50000000, "small_company_max_fixed_assets_ngn": 250000000, "small_rate_percent": 0, "large_rate_percent": 30, "professional_services_small_company_exclusion": True},
    },
}


def _parse(value: str) -> date:
    return date.fromisoformat(value)


def calculator_rule_metadata(tax_type: str) -> Dict[str, Any]:
    code = str(tax_type or "").strip().lower()
    rule = dict(_RULES.get(code) or {})
    today = datetime.now(timezone.utc).date()
    review_by = _parse(CALCULATOR_RULES_REVIEW_BY)
    stale = today > review_by
    return {
        "rule_pack_version": CALCULATOR_RULE_PACK_VERSION,
        "last_reviewed_at": CALCULATOR_RULES_LAST_REVIEWED,
        "review_by": CALCULATOR_RULES_REVIEW_BY,
        "review_status": "review_required" if stale else "approved_for_v1",
        "stale": stale,
        "tax_type": code,
        **rule,
        "ai_called": False,
        "usage_charged": False,
        "credits_consumed": 0,
        "disclaimer": "Estimate only. Confirm taxpayer classification, allowable deductions, exemptions and the applicable assessment period before filing or payment.",
    }


def attach_calculator_metadata(tax_type: str, result: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(result or {})
    meta = calculator_rule_metadata(tax_type)
    out["calculator_rule"] = meta
    out["calculation_mode"] = "deterministic_free"
    out["ai_called"] = False
    out["usage_charged"] = False
    out["credits_consumed"] = 0
    if meta.get("stale"):
        out["warning"] = "This calculator rule pack is due for legal/tax review. Verify current rules before relying on the estimate."
    return out
