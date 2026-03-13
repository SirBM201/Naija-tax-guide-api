# app/services/ask_router_service.py
from __future__ import annotations

from typing import Dict

from app.services.tax_intent_service import classify_tax_intent, build_intent_meta
from app.services.tax_process_composer import try_compose


def route_tax_question(question: str) -> Dict | None:
    """
    Determines if a question can be answered using
    deterministic tax logic instead of AI.
    """

    intent = classify_tax_intent(question)

    if not intent:
        return None

    composed = try_compose(intent)

    if composed:
        result = {
            "ok": True,
            "answer": composed["answer"],
            "meta": composed["meta"],
        }
        return result

    return None
