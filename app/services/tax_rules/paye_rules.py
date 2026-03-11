from __future__ import annotations

from typing import Optional


def explain_paye_basic() -> str:
    return (
        "PAYE in Nigeria stands for Pay As You Earn. It is the system under which employers deduct personal income tax from employees' salaries or wages "
        "and remit the tax to the relevant tax authority."
    )


def can_handle_paye_rule(question: str, topic: str, intent_type: str) -> bool:
    return topic == "paye" and intent_type == "definition"


def resolve_paye_rule(question: str, intent_type: str) -> Optional[str]:
    if intent_type == "definition":
        return explain_paye_basic()
    return None
