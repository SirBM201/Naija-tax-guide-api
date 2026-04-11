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
            "which authority",
            "who handles",
            "who issues",
            "who receives",
            "who should receive",
            "does firs or state",
            "does nrs or state",
            "which portal should i use",
            "which portal do i use",
        ],
    ):
        return "authority"

    if _contains_any(
        ql,
        [
            "what records should i keep",
            "what records should be kept",
            "what documents should i keep",
            "what documents are needed",
            "what documents are required",
            "records should i keep",
            "records should be kept",
            "documentation",
            "supporting documents",
            "evidence",
        ],
    ):
        return "records"

    if _contains_any(
        ql,
        [
            "how do i verify",
            "how to verify",
            "verify",
            "verification",
            "validate",
            "validation",
            "check tin",
        ],
    ):
        return "verification"

    if _contains_any(
        ql,
        [
            "how do i register",
            "how to register",
            "register for",
            "registration",
            "apply for",
            "how can i register",
            "how can i apply",
            "get a tin",
            "obtain a tin",
        ],
    ):
        return "registration"

    if _contains_any(
        ql,
        [
            "how do i file",
            "how to file",
            "how can i file",
            " file ",
            " filing",
            "return",
            "submit",
            "submission",
        ],
    ):
        return "filing"

    if _contains_any(
        ql,
        [
            "how do i pay",
            "how to pay",
            "how do i remit",
            "how to remit",
            "how can i pay",
            "how can i remit",
            "payment",
            " remit",
            "remittance",
        ],
    ):
        return "payment"

    if _contains_any(
        ql,
        [
            "exempt",
            "exemption",
            "zero rated",
            "zero-rated",
            "outside scope",
        ],
    ):
        return "exemption"

    if _contains_any(
        ql,
        [
            "calculate",
            "rate",
            "how much",
            "penalty",
            "due date",
            "deadline",
            "fine",
            "late fee",
            "percentage",
            "when is",
        ],
    ):
        return "rate"

    if _contains_any(
        ql,
        [
            "what is",
            "meaning of",
            "stands for",
            "define",
            "difference between",
            "explain",
            "what does",
        ],
    ):
        return "definition"

    if _contains_any(
        ql,
        [
            "do i need to",
            "am i required to",
            "must i",
            "must we",
            "who should",
            "does it apply",
            "am i supposed to",
            "is it compulsory",
            "should i charge",
            "do i have to",
            "who pays",
            "who must pay",
            "who should pay",
            "who must comply",
            "comply with",
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
            "how do i",
            "how to",
            "steps to",
            "process for",
            "procedure for",
        ],
    ):
        return "procedure"

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
    ql = q.lower()

    if "tax clearance certificate" in ql or re.search(r"\btcc\b", ql):
        return "tax_clearance_certificate"
    if "tax identification number" in ql or re.search(r"\btin\b", ql):
        return "tin"
    if "value added tax" in ql or re.search(r"\bvat\b", ql):
        return "vat"
    if "company income tax" in ql or re.search(r"\bcit\b", ql):
        return "company_income_tax"
    if "personal income tax" in ql or re.search(r"\bpit\b", ql):
        return "personal_income_tax"
    if "paye" in ql or "pay as you earn" in ql:
        return "paye"
    if "withholding" in ql or re.search(r"\bwht\b", ql):
        return "withholding_tax"
    if "freelancer" in ql or "sole proprietor" in ql or "self employed" in ql:
        return "freelancer_tax"
    if "registration" in ql or "register" in ql:
        return "registration"
    if "file" in ql or "filing" in ql or "return" in ql or "submit" in ql:
        return "filing"
    if "penalty" in ql or "fine" in ql or "deadline" in ql or "due date" in ql:
        return "penalty"
    if "deduct" in ql or "allowable" in ql or "expense" in ql:
        return "deduction"
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

    if any(x in ql for x in ["and", "or"]) and len(tokens) >= 12:
        return "intermediate"

    return "basic"


def _risk_level(intent_type: str, complexity: str) -> str:
    if complexity == "advanced" or intent_type in {"advanced_advisory", "rate"}:
        return "high"
    if complexity == "intermediate" or intent_type in {"obligation", "deduction", "authority"}:
        return "medium"
    return "low"


def _requires_clarification(intent_type: str, topic: str, complexity: str, q: str) -> bool:
    ql = q.lower()

    if intent_type == "advanced_advisory" or complexity == "advanced":
        return True

    if intent_type in {"definition", "authority", "records", "verification", "registration", "filing", "payment", "rate", "exemption"}:
        return False

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

    if intent_type in {"obligation", "deduction"} and topic == "general":
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
