from __future__ import annotations

from typing import Dict, Any, List

from app.schemas.ask_models import QueryClassification, RetrievalCandidate
from .retrieval_service import gather_candidates
from .candidate_ranker import rank_candidates


def retrieve_ranked_candidates(classification: QueryClassification) -> List[RetrievalCandidate]:
    candidates = gather_candidates(classification)
    ranked = rank_candidates(classification, candidates)
    return ranked


def ranked_debug_dump(ranked: List[RetrievalCandidate]) -> List[Dict[str, Any]]:
    return [
        {
            "candidate_id": c.candidate_id,
            "question": c.question,
            "canonical_key": c.canonical_key,
            "intent_type": c.intent_type,
            "topic": c.topic,
            "review_status": c.review_status,
            "trust_score": c.trust_score,
            "similarity": c.similarity,
            "match_type": c.match_type,
            "rank_score": c.rank_score,
        }
        for c in ranked
    ]
