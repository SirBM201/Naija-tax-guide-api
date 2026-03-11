# app/services/candidate_ranker.py
from __future__ import annotations

from typing import Dict, Any, List


MIN_TRUST = 0.78


def _intent_compatible(query_intent: str, cand_intent: str) -> bool:
    if query_intent == cand_intent:
        return True

    compatible = {
        "definition": {"definition", "general"},
        "how_to": {"how_to"},
        "deduction": {"deduction", "general"},
        "calculation": {"calculation"},
        "advanced_advisory": {"advanced_advisory"},
        "general": {"general", "definition", "how_to"},
    }
    return cand_intent in compatible.get(query_intent, set())


def _passes_filters(query: Dict[str, Any], cand: Dict[str, Any]) -> bool:
    if (cand.get("review_status") or "").lower() != "approved":
        return False

    if float(cand.get("trust_score") or 0) < MIN_TRUST:
        return False

    if cand.get("topic") not in {query.get("topic"), "general", None, ""}:
        return False

    if not _intent_compatible(query.get("intent_type", "general"), cand.get("intent_type", "general")):
        return False

    if cand.get("jurisdiction") not in {query.get("jurisdiction"), "global", None, ""}:
        return False

    return True


def _score(query: Dict[str, Any], cand: Dict[str, Any]) -> float:
    score = 0.0

    if cand.get("match_type") == "exact":
        score += 40
    if cand.get("canonical_key") == query.get("canonical_key"):
        score += 25
    if cand.get("topic") == query.get("topic"):
        score += 20
    if cand.get("intent_type") == query.get("intent_type"):
        score += 15
    if cand.get("jurisdiction") == query.get("jurisdiction"):
        score += 10

    score += float(cand.get("trust_score") or 0) * 20
    score += float(cand.get("source_authority_score") or 0) * 10
    score += float(cand.get("similarity") or 0) * 15

    return score


def rank_candidates(query: Dict[str, Any], candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    filtered = [c for c in candidates if _passes_filters(query, c)]

    for c in filtered:
        c["_rank_score"] = _score(query, c)

    filtered.sort(key=lambda x: x.get("_rank_score", 0), reverse=True)
    return filtered
