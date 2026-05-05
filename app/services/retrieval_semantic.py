from __future__ import annotations

from typing import List

from app.repositories.qa_embedding_repo import semantic_search
from app.schemas.ask_models import QueryClassification, RetrievalCandidate


def retrieve_semantic_candidates(classification: QueryClassification) -> List[RetrievalCandidate]:
    rows = semantic_search(
        classification.raw_question,
        lang=classification.lang,
        jurisdiction=classification.jurisdiction,
        limit=5,
    )

    out: List[RetrievalCandidate] = []
    for row in rows:
        out.append(
            RetrievalCandidate(
                candidate_id=str(row.get("id") or ""),
                source_table="qa_embeddings",
                source_type=str(row.get("source_type") or "semantic"),
                question=str(row.get("question") or ""),
                answer=str(row.get("answer") or ""),
                canonical_key=row.get("canonical_key"),
                normalized_question=row.get("normalized_question"),
                intent_type=str(row.get("intent_type") or "general"),
                topic=str(row.get("topic") or "general"),
                jurisdiction=str(row.get("jurisdiction") or "nigeria"),
                lang=str(row.get("lang") or "en"),
                trust_score=float(row.get("trust_score") or 0),
                review_status=str(row.get("review_status") or "pending"),
                source_authority_score=float(row.get("source_authority_score") or 0),
                similarity=float(row.get("similarity") or 0),
                match_type="semantic",
            )
        )
    return out
