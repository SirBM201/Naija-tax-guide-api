# app/services/decision_engine.py
from __future__ import annotations

from typing import Dict, Any, List


def decide_answer_mode(
    classification: Dict[str, Any],
    ranked_candidates: List[Dict[str, Any]],
    *,
    has_ai_credit: bool,
    monthly_ai_usage: int,
    monthly_ai_limit: int,
) -> Dict[str, Any]:
    best = ranked_candidates[0] if ranked_candidates else None

    if classification.get("requires_clarification"):
        return {"mode": "clarification", "best_candidate": best}

    if classification.get("intent_type") == "calculation":
        return {"mode": "rules_engine", "best_candidate": best}

    if best and best.get("_rank_score", 0) >= 85:
        return {"mode": "direct_cache", "best_candidate": best}

    if best and best.get("_rank_score", 0) >= 70 and has_ai_credit:
        return {"mode": "grounded_synthesis", "best_candidate": best}

    if best and not has_ai_credit:
        return {"mode": "direct_cache", "best_candidate": best}

    if not best and not has_ai_credit:
        return {"mode": "insufficient_credits_uncached", "best_candidate": None}

    return {"mode": "grounded_synthesis", "best_candidate": best}
