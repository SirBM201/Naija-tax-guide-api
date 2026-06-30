# app/services/tax_source_catalog.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

RiskLevel = Literal["low", "medium", "high"]
SourceType = Literal["law", "regulation", "authority_guidance", "state_practice", "internal_review"]


@dataclass(frozen=True)
class TaxSourceCategory:
    code: str
    label: str
    source_type: SourceType
    risk_level: RiskLevel
    description: str
    review_rule: str


SOURCE_CATEGORIES: tuple[TaxSourceCategory, ...] = (
    TaxSourceCategory(
        code="primary_law",
        label="Primary law and regulations",
        source_type="law",
        risk_level="high",
        description="Acts, regulations, and official tax instruments used for rates, thresholds, obligations, penalties, and deadlines.",
        review_rule="Review before relying on numeric or deadline claims, especially after law reforms or fiscal updates.",
    ),
    TaxSourceCategory(
        code="federal_authority_guidance",
        label="Federal tax authority guidance",
        source_type="authority_guidance",
        risk_level="high",
        description="Federal circulars, notices, portal instructions, and public guidance from the relevant federal tax authority.",
        review_rule="Verify current applicability before giving filing routes, portal links, due dates, penalties, or compliance instructions.",
    ),
    TaxSourceCategory(
        code="state_authority_practice",
        label="State tax authority practice",
        source_type="state_practice",
        risk_level="high",
        description="State-level administration for PAYE, personal income tax, residence, notices, and remittance practice.",
        review_rule="Do not assume one state process applies nationally. Ask for state of residence or operation where relevant.",
    ),
    TaxSourceCategory(
        code="reviewed_internal_answer",
        label="Reviewed internal knowledge",
        source_type="internal_review",
        risk_level="medium",
        description="Curated answers reviewed for common Nigerian tax questions and product guidance flows.",
        review_rule="Attach jurisdiction, risk level, source category, and last-reviewed date before surfacing as reviewed guidance.",
    ),
)


def get_source_category(code: str) -> TaxSourceCategory | None:
    clean = (code or "").strip().lower()
    for category in SOURCE_CATEGORIES:
        if category.code == clean:
            return category
    return None


def high_risk_source_codes() -> list[str]:
    return [category.code for category in SOURCE_CATEGORIES if category.risk_level == "high"]


def source_review_summary() -> list[dict[str, str]]:
    return [
        {
            "code": category.code,
            "label": category.label,
            "source_type": category.source_type,
            "risk_level": category.risk_level,
            "review_rule": category.review_rule,
        }
        for category in SOURCE_CATEGORIES
    ]
