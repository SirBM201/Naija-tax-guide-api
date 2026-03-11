# app/services/query_classifier.py
from __future__ import annotations

import re
from typing import Dict, Any


def _normalize(text: str) -> str:
    t = (text or "").strip().lower()
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"[^\w\s]", "", t)
    return t.strip()


def _detect_intent(q: str) -> str:
    ql = q.lower()

    if any(x in ql for x in ["what is", "meaning of", "stands for", "define"]):
        return "definition"
    if any(x in ql for x in ["how do i", "how to", "steps to", "register for"]):
        return "how_to"
    if any(x in ql for x in ["can i deduct", "deduct", "allowable expense"]):
        return "deduction"
    if any(x in ql for x in ["calculate", "rate", "how much", "penalty", "due date"]):
        return "calculation"
    if any(x in ql for x in ["should i", "best structure", "multi-branch", "optimize"]):
        return "advanced_advisory"
    return "general"


def _detect_topic(q: str) -> str:
    ql = q.lower()

    if "vat" in ql or "value added tax" in ql:
        return "vat"
    if "paye" in ql or "pay as you earn" in ql:
        return "paye"
    if "withholding" in ql or "wht" in ql:
        return "withholding_tax"
    if "company income tax" in ql or "cit" in ql:
        return "cit"
    if "freelancer" in ql or "sole proprietor" in ql:
        return "freelancer_tax"
    if "penalty" in ql or "fine" in ql:
        return "penalty"
    return "general"


def _detect_complexity(q: str) -> str:
    ql = q.lower()
    if any(x in ql for x in ["multi-branch", "group structure", "cross-border", "state and federal"]):
        return "advanced"
    if len(ql.split()) > 12:
        return "intermediate"
    return "basic"


def _requires_clarification(intent_type: str, complexity: str) -> bool:
    return intent_type in {"advanced_advisory"} or complexity == "advanced"


def classify_query(question: str, lang: str = "en") -> Dict[str, Any]:
    normalized = _normalize(question)
    intent_type = _detect_intent(normalized)
    topic = _detect_topic(normalized)
    complexity = _detect_complexity(normalized)

    return {
        "raw_question": question,
        "normalized_question": normalized,
        "canonical_key": normalized[:120].replace(" ", "_"),
        "intent_type": intent_type,
        "topic": topic,
        "jurisdiction": "nigeria",
        "complexity": complexity,
        "risk_level": "high" if complexity == "advanced" else "medium" if complexity == "intermediate" else "low",
        "requires_clarification": _requires_clarification(intent_type, complexity),
        "lang": lang or "en",
    }
