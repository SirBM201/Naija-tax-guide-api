from __future__ import annotations

from app.schemas.ask_models import RetrievalCandidate


def candidate_confidence(candidate: RetrievalCandidate | None) -> float:
    if not candidate:
        return 0.0
    return float(candidate.rank_score or 0.0)
