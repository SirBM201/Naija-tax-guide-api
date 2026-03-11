from __future__ import annotations

from typing import List

from app.schemas.ask_models import DecisionResult, QueryClassification, RetrievalCandidate


def decide_answer_mode(
    classification: QueryClassification,
    ranked_candidates: List[RetrievalCandidate],
    *,
    has_ai_credit: bool,
    monthly_ai_usage: int,
    monthly_ai_limit: int,
) -> DecisionResult:
    best = ranked_candidates[0] if ranked_candidates else None

    reasons = [
        f"intent={classification.intent_type}",
        f"topic={classification.topic}",
        f"complexity={classification.complexity}",
        f"has_ai_credit={has_ai_credit}",
        f"monthly_ai_usage={monthly_ai_usage}",
        f"monthly_ai_limit={monthly_ai_limit}",
    ]

    if classification.requires_clarification:
        return DecisionResult(mode="clarification", best_candidate=best, reasons=reasons + ["requires_clarification=true"])

    if classification.intent_type == "calculation":
        return DecisionResult(mode="rules_engine", best_candidate=best, reasons=reasons + ["intent=calculation"])

    if best and best.rank_score >= 85:
        return DecisionResult(mode="direct_cache", best_candidate=best, reasons=reasons + [f"best_rank={best.rank_score}"])

    if best and best.rank_score >= 70 and has_ai_credit:
        return DecisionResult(mode="grounded_synthesis", best_candidate=best, reasons=reasons + [f"best_rank={best.rank_score}"])

    if best and not has_ai_credit:
        return DecisionResult(mode="direct_cache", best_candidate=best, reasons=reasons + ["credits_exhausted_but_cache_available"])

    if not best and not has_ai_credit:
        return DecisionResult(mode="insufficient_credits_uncached", best_candidate=None, reasons=reasons + ["no_cache_and_no_ai_credit"])

    return DecisionResult(mode="grounded_synthesis", best_candidate=best, reasons=reasons + ["fallback_grounded_synthesis"])
