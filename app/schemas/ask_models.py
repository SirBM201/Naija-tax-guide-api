from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class QueryClassification:
    raw_question: str
    normalized_question: str
    canonical_key: str
    intent_type: str
    topic: str
    jurisdiction: str
    complexity: str
    risk_level: str
    requires_clarification: bool
    lang: str = "en"


@dataclass
class RetrievalCandidate:
    candidate_id: str
    source_table: str
    source_type: str
    question: str
    answer: str
    canonical_key: Optional[str] = None
    normalized_question: Optional[str] = None
    intent_type: str = "general"
    topic: str = "general"
    jurisdiction: str = "nigeria"
    lang: str = "en"
    trust_score: float = 0.0
    review_status: str = "pending"
    source_authority_score: float = 0.0
    similarity: float = 0.0
    match_type: str = "unknown"
    extra: Dict[str, Any] = field(default_factory=dict)
    rank_score: float = 0.0


@dataclass
class DecisionResult:
    mode: str
    best_candidate: Optional[RetrievalCandidate] = None
    reasons: List[str] = field(default_factory=list)


@dataclass
class AskExecutionResult:
    ok: bool
    answer: Optional[str] = None
    error: Optional[str] = None
    fix: Optional[str] = None
    mode: Optional[str] = None
    confidence: Optional[float] = None
    debug: Dict[str, Any] = field(default_factory=dict)
