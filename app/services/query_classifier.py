from __future__ import annotations

import re
from app.schemas.ask_models import QueryClassification


def _normalize(text: str) -> str:
    t = (text or "").strip().lower()
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def _canonical_key(normalized: str) -> str:
    if not normalized:
        return "empty"
    return normalized[:120].replace(" ", "_")


def _contains_any(text: str, phrases: list[str]) -> bool:
    return any(p in text for p in phrases)


def _detect_intent(q: str) -> str:
    ql = q.lower()

    if _contains_any(
        ql,
        [
            "which tax authority",
            "what tax authority",
            "who handles",
            "which authority",
            "does firs or state",
            "does nrs or state",
            "who issues",
            "who receives",
            "which portal should i use",
            "which portal do i use",
        ],
    ):
        return "authority"

    if _contains_any(
        ql,
        [
            "verify",
            "verification",
            "validate",
            "validation",
            "check tin",
            "confirm tcc",
            "tcc verification",
        ],
    ):
        return "verification"

    if _contains_any(
        ql,
        [
            "what documents",
            "documents needed",
            "documents required",
            "requirements for",
        ],
    ):
        return "documents"

    if _contains_any(
        ql,
        [
            "what records",
            "what record",
            "what should i keep",
            "records should i keep",
            "keep for",
        ],
    ):
        return "records"

    if _contains_any(
        ql,
        [
            "what is",
            "meaning of",
            "stands for",
            "define",
            "explain",
            "what does",
            "difference between",
        ],
    ):
        return "definition"

    if _contains_any(
        ql,
        [
            "rate",
            "percentage",
        ],
    ):
        return "rate"

    if _contains_any(
        ql,
        [
            "register for",
            "registration",
            "register ",
            "apply for a tin",
            "get a tin",
            "obtain a tin",
        ],
    ):
        return "registration"

    if _contains_any(
        ql,
        [
            "how do i",
            "how to",
            "steps to",
            "process for",
            "procedure for",
            "how can i",
        ],
    ):
        return "procedure"

    if _contains_any(
        ql,
        [
            "file",
            "filing",
            "submit",
            "return",
        ],
    ):
        return "filing"

    if _contains_any(
        ql,
        [
            "pay",
            "payment",
            "remit",
            "remittance",
            "settle",
        ],
    ):
        return "payment"

    if _contains_any(
        ql,
        [
            "do i need to",
            "am i required to",
            "must i",
            "must we",
            "who must",
            "who should",
            "who needs to",
            "does it apply",
            "should i charge",
            "comply with",
            "is it compulsory",
            "do i have to",
        ],
    ):
        return "obligation"

    if _contains_any(
        ql,
        [
            "can i deduct",
            "is this deductible",
            "allowable expense",
            "can i claim",
            "deduct",
            "deductible",
            "allowable",
        ],
    ):
        return "deduction"

    if _contains_any(
        ql,
        [
            "calculate",
            "computation",
            "compute",
            "how much",
            "penalty",
            "due date",
            "deadline",
            "fine",
            "late fee",
            "when is",
        ],
    ):
        return "calculation"

    if _contains_any(
        ql,
        [
            "structure",
            "multi branch",
            "cross border",
            "optimize",
            "advisory",
            "holding company",
            "group company",
            "international",
            "non resident",
            "double taxation",
        ],
    ):
        return "advanced_advisory"

    return "general"


def _detect_topic(q: str) -> str:
    ql = f" {q.lower()} "

    if " tax clearance certificate " in ql or " tcc " in ql:
        return "tax_clearance_certificate"
    if " tax identification number " in ql or " tin " in ql or " tax id " in ql:
        return "tin"
    if " value added tax " in ql or " vat " in ql:
        return "vat"
    if " pay as you earn " in ql or " paye " in ql or " payroll " in ql:
        return "paye"
    if " withholding tax " in ql or " wht " in ql or " withholding " in ql:
        return "withholding_tax"
    if " company income tax " in ql or re.search(r"\bcit\b", ql):
        return "company_income_tax"
    if " personal income tax " in ql or re.search(r"\bpit\b", ql):
        return "personal_income_tax"
    if " freelancer " in ql or " sole proprietor " in ql or " self employed " in ql:
        return "freelancer_tax"

    return "general"


def _detect_complexity(q: str) -> str:
    ql = q.lower()
    tokens = ql.split()

    if _contains_any(
        ql,
        [
            "multi branch",
            "cross border",
            "group structure",
            "holding company",
            "state and federal",
            "non resident",
            "double taxation",
            "multiple business",
        ],
    ):
        return "advanced"

    if len(tokens) >= 18:
        return "intermediate"

    if any(x in ql for x in [" and ", " or "]) and len(tokens) >= 12:
        return "intermediate"

    return "basic"


def _risk_level(intent_type: str, complexity: str) -> str:
    if complexity == "advanced" or intent_type in {"advanced_advisory", "calculation"}:
        return "high"
    if complexity == "intermediate" or intent_type in {"obligation", "deduction", "authority"}:
        return "medium"
    return "low"


def _requires_clarification(intent_type: str, topic: str, complexity: str, q: str) -> bool:
    ql = q.lower()

    if intent_type == "advanced_advisory" or complexity == "advanced":
        return True

    mixed_signals = 0
    for group in [
        ["register", "registration", "tin"],
        ["file", "filing", "return", "submit"],
        ["pay", "payment", "remit", "remittance"],
        ["penalty", "fine", "late"],
    ]:
        if any(g in ql for g in group):
            mixed_signals += 1
    if mixed_signals >= 3:
        return True

    if intent_type in {"obligation", "deduction", "authority"} and topic == "general":
        return True

    if any(x in ql for x in ["this business", "my business", "my company", "as a business"]) and not any(
        x in ql for x in ["freelancer", "sole proprietor", "company", "employee", "employer"]
    ):
        return True

    return False


def classify_query(question: str, lang: str = "en") -> QueryClassification:
    normalized = _normalize(question)
    intent_type = _detect_intent(normalized)
    topic = _detect_topic(normalized)
    complexity = _detect_complexity(normalized)

    return QueryClassification(
        raw_question=question,
        normalized_question=normalized,
        canonical_key=_canonical_key(normalized),
        intent_type=intent_type,
        topic=topic,
        jurisdiction="nigeria",
        complexity=complexity,
        risk_level=_risk_level(intent_type, complexity),
        requires_clarification=_requires_clarification(intent_type, topic, complexity, normalized),
        lang=(lang or "en").strip().lower(),
    )
