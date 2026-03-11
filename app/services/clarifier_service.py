from __future__ import annotations

from app.schemas.ask_models import QueryClassification


def needs_clarification(classification: QueryClassification) -> bool:
    return bool(classification.requires_clarification)
