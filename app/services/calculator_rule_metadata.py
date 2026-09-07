from __future__ import annotations

"""Version and provenance metadata for deterministic V1 tax calculators.

This module deliberately separates calculation mechanics from legal-rule
provenance. Calculator execution remains free/non-AI. A rule pack must be
reviewed before its freshness window expires; stale packs are disclosed in the
result instead of silently presenting hard-coded values as timeless law.
"""

from datetime import date, datetime, timezone
from typing import Any, Dict

CALCULATOR_RULE_PACK_VERSION = "ng-v1-legacy-rules-2026-09-07"
CALCULATOR_RULES_LAST_REVIEWED = "2026-09-07"
CALCULATOR_RULES_REVIEW_BY = "2026-12-31"

_RULES: Dict[str, Dict[str, Any]] = {
    "paye": {
        "jurisdiction": "Nigeria",
        "rule_family": "Personal Income Tax / PAYE",
        "source_reference": "Naija Tax Guide approved calculator rule pack",
        "effective_period": "legacy PITA rule set; verify applicability for the taxpayer period",
        "values": {
            "cra": "higher of NGN 200,000 or 1% of annual gross income, plus 20% of annual gross income",
            "bands": ["NGN 300,000 @ 7%", "next NGN 300,000 @ 11%", "next NGN 500,000 @ 15%", "next NGN 500,000 @ 19%", "next NGN 1,600,000 @ 21%", "remainder @ 24%"],
        },
    },
    "vat": {
        "jurisdiction": "Nigeria",
        "rule_family": "Value Added Tax",
        "source_reference": "Naija Tax Guide approved calculator rule pack",
        "effective_period": "rule-pack period shown by this calculator; verify special/exempt supplies separately",
        "values": {"standard_rate_percent": 7.5},
    },
    "cit": {
        "jurisdiction": "Nigeria",
        "rule_family": "Companies Income Tax",
        "source_reference": "Naija Tax Guide approved calculator rule pack",
        "effective_period": "legacy Finance Act turnover bands; verify applicability for the company accounting period",
        "values": {"small_company_max_revenue_ngn": 25000000, "medium_company_max_revenue_ngn": 100000000, "small_rate_percent": 0, "medium_rate_percent": 20, "large_rate_percent": 30},
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
        "disclaimer": "Estimate only. Confirm the applicable tax law, taxpayer classification, exemptions and effective period before filing or payment.",
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
